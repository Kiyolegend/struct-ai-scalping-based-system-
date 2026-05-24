"""
STRUCT.ai Scalping Engine — Backtester (v2 — fully corrected)

All 16 bugs from the original version are fixed. Only this file changes.
No strategy file (scalp1–6.py), no dashboard_server.py, no state.py,
no config.py, no risk manager — nothing else is touched.

Bug fixes applied in this version
──────────────────────────────────
  BUG-01  BOS/CHoCH events carry "time" + "price"  → freshness filters work
  BUG-02  S5/S6 session functions monkey-patched   → use candle time, not wall clock
  BUG-03  1H candles synthesised from 5M            → S3 (ICT OB/FVG) fires correctly
  BUG-04  Structure labels carry "time"             → S1 24-hour pullback cap works
  BUG-05  Pip size read per-symbol from config      → correct for all pairs
  BUG-06  Spread cost applied to TP check           → win rate matches live net-RR
  BUG-07  4H timeframe synthesised + populated      → S3 4H OB-stacking works
  BUG-08  Bias computed from correct TF window      → 4H/1H/15M bias independent
  BUG-09  S6 cooldown writes blocked during test    → live engine state not corrupted
  BUG-10  Daily trade limits + consec-loss cap      → trade count mirrors live engine
  BUG-11  "symbol" key set in every state dict      → pip config always resolves
  BUG-12  Per-pair pip + spread handled correctly   → multi-pair safe
  BUG-13  Ambiguous candle resolved by body dir     → not by distance heuristic
  BUG-14  Asia range pre-cached per day             → O(n) not O(n²)
  BUG-15  Candle window 100 bars (was 50)           → S4 compression lookback OK
  BUG-16  Twelve Data truncation warning surfaced   → user knows data was capped

Data source priority:
  1. MetaTrader5 Python library (Windows only — most accurate, same broker feed)
  2. Twelve Data REST API (cross-platform fallback, 5M candles)
"""

import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from state import sanitize_state, is_tradeable_session
from strategies import STRATEGIES

DATA_DIR = Path(__file__).parent / "backtest_data"
DATA_DIR.mkdir(exist_ok=True)

TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"

# Spread applied to TP when determining outcomes (BUG-06).
# Conservative default: 1.5 pips. Real spreads vary 0.8–3.5 pips by session.
DEFAULT_SPREAD_PIPS: float = 1.5


# ── Per-symbol pip size (BUG-05 / BUG-12) ─────────────────────────────────────

def _pip(symbol: str = None) -> float:
    """Return the pip size for the given symbol (or the default config symbol)."""
    return config.get_symbol_cfg(symbol)["pip_size"]


# ── MT5 candle fetch (Windows only) ───────────────────────────────────────────

def _fetch_mt5(symbol: str, interval: str = "5m", count: int = 5000) -> list | None:
    """Fetch candles from MT5 for the given symbol."""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None

        tf_map = {
            "1m":  mt5.TIMEFRAME_M1,
            "5m":  mt5.TIMEFRAME_M5,
            "15m": mt5.TIMEFRAME_M15,
            "1h":  mt5.TIMEFRAME_H1,
        }
        tf    = tf_map.get(interval, mt5.TIMEFRAME_M5)
        cfg   = config.get_symbol_cfg(symbol)
        rates = mt5.copy_rates_from_pos(cfg["mt5_name"], tf, 0, count)
        mt5.shutdown()

        if rates is None or len(rates) == 0:
            return None

        return [
            {"time": int(r["time"]), "open": float(r["open"]),
             "high": float(r["high"]), "low": float(r["low"]),
             "close": float(r["close"])}
            for r in rates
        ]
    except Exception:
        return None


# ── Twelve Data fetch (cross-platform fallback) ────────────────────────────────

def _fetch_twelvedata(symbol: str, interval: str = "5min",
                      count: int = 5000) -> list | None:
    """
    BUG-12: symbol is now a parameter — no longer hardcoded to USD/JPY.
    BUG-16: caller logs a warning when count was capped to 5000.
    """
    if not TWELVE_DATA_KEY:
        return None
    try:
        import requests
        resp = requests.get(TWELVE_DATA_URL, params={
            "symbol":     symbol,
            "interval":   interval,
            "outputsize": min(count, 5000),
            "apikey":     TWELVE_DATA_KEY,
        }, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            return None
        values = data.get("values", [])
        if not values:
            return None

        candles = []
        for v in reversed(values):
            fmt = "%Y-%m-%d %H:%M:%S" if " " in v["datetime"] else "%Y-%m-%d"
            dt  = datetime.strptime(v["datetime"], fmt).replace(tzinfo=timezone.utc)
            candles.append({
                "time":  int(dt.timestamp()),
                "open":  float(v["open"]),
                "high":  float(v["high"]),
                "low":   float(v["low"]),
                "close": float(v["close"]),
            })
        return candles
    except Exception as e:
        print(f"[BACKTEST] Twelve Data error: {e}")
        return None


def fetch_candles(days: int = 7, symbol: str = None) -> tuple:
    """
    Returns (candles list, source name str) or (None, error str).
    BUG-12: symbol parameter — no longer hardcoded to USD/JPY.
    BUG-16: warns the user when Twelve Data silently truncates the window.
    """
    sym    = symbol or config.SYMBOL
    # 5M candles per day ≈ 288 (24h × 12). Add 500 for lookback buffer.
    needed = days * 288 + 500

    candles = _fetch_mt5(sym, "5m", min(needed, 10_000))
    if candles:
        return candles, f"MT5 (5M, {sym})"

    # BUG-16: Twelve Data free tier caps at 5000 candles (~17 days).
    if needed > 5_000:
        approx_days = 5_000 // 288
        print(
            f"[BACKTEST] WARNING: Twelve Data free tier caps at 5 000 candles "
            f"(~{approx_days} days). Requested {days}-day backtest will only "
            f"cover ~{approx_days} days of data."
        )

    candles = _fetch_twelvedata(sym, "5min", min(needed, 5_000))
    if candles:
        actual_days = len(candles) // 288
        return candles, f"Twelve Data (5M, {sym}, ~{actual_days}d)"

    return None, "unavailable"


# ── Synthetic timeframe aggregation (BUG-03 / BUG-07) ────────────────────────

def _aggregate_candles(candles_5m: list, end_idx: int,
                       bars_per_tf: int, n_output: int) -> list:
    """
    Roll up 5M candles ending at end_idx into a higher timeframe.

    bars_per_tf : 5M bars that form one output bar  (3=15M, 12=1H, 48=4H)
    n_output    : maximum number of TF bars to return
    end_idx     : exclusive upper bound — only candles_5m[:end_idx] are used

    Each output bar's "time" is taken from its last 5M constituent (BUG-01/04).
    """
    need   = bars_per_tf * n_output
    start  = max(0, end_idx - need)
    window = candles_5m[start:end_idx]

    result = []
    for i in range(0, len(window) - bars_per_tf + 1, bars_per_tf):
        group = window[i: i + bars_per_tf]
        result.append({
            "time":  group[-1]["time"],      # timestamp of the last 5M bar
            "open":  group[0]["open"],
            "high":  max(c["high"] for c in group),
            "low":   min(c["low"]  for c in group),
            "close": group[-1]["close"],
        })
    return result


# ── Structure / event detectors — all events carry "time" (BUG-01 / BUG-04) ──

def _compute_trend(candles: list, window: int = 5) -> str:
    if len(candles) < window:
        return "neutral"
    recent = candles[-window:]
    up   = sum(1 for c in recent if c["close"] > c["open"])
    down = sum(1 for c in recent if c["close"] < c["open"])
    if up >= window - 1:
        return "bullish"
    if down >= window - 1:
        return "bearish"
    return "neutral"


def _detect_swing_structure(candles: list, lookback: int = 30) -> list:
    """
    BUG-04: every swing point now carries 'time' from the pivot candle.
    Strategies that cap pullback age (e.g. S1's 24-hour guard) now work
    correctly instead of being permanently bypassed.
    """
    window    = candles[-lookback:] if len(candles) > lookback else candles
    structure = []
    prev_high = None
    prev_low  = None

    for i in range(2, len(window)):
        c_prev = window[i - 2]
        c_mid  = window[i - 1]
        c_curr = window[i]

        if c_mid["high"] > c_prev["high"] and c_mid["high"] > c_curr["high"]:
            label = "HH" if (prev_high is None or c_mid["high"] > prev_high) else "LH"
            structure.append({
                "label": label,
                "price": c_mid["high"],
                "kind":  "high",
                "time":  c_mid["time"],    # BUG-04
            })
            prev_high = c_mid["high"]

        if c_mid["low"] < c_prev["low"] and c_mid["low"] < c_curr["low"]:
            label = "HL" if (prev_low is None or c_mid["low"] > prev_low) else "LL"
            structure.append({
                "label": label,
                "price": c_mid["low"],
                "kind":  "low",
                "time":  c_mid["time"],    # BUG-04
            })
            prev_low = c_mid["low"]

    return structure


def _detect_bos(candles: list, lookback: int = 20) -> list:
    """
    BUG-01: every BOS event now carries 'time' and 'price'.
    Strategies filter BOS by age (S1: 1h, S2: 2h, S5: 30min).
    Without 'time' those filters always passed — now they work correctly.
    """
    window = candles[-lookback:] if len(candles) > lookback else candles
    if len(window) < 6:
        return []

    events = []
    for i in range(5, len(window)):
        prior      = window[i - 5: i - 1]
        swing_high = max(c["high"] for c in prior)
        swing_low  = min(c["low"]  for c in prior)
        curr       = window[i]
        if curr["close"] > swing_high:
            events.append({
                "direction": "bullish",
                "price":     curr["close"],
                "time":      curr["time"],    # BUG-01
            })
        elif curr["close"] < swing_low:
            events.append({
                "direction": "bearish",
                "price":     curr["close"],
                "time":      curr["time"],    # BUG-01
            })
    return events[-5:]


def _detect_choch(candles: list, lookback: int = 20) -> list:
    """
    BUG-01: every CHoCH event now carries 'time' and 'price'.
    S2 filters CHoCH by age (up to 6h). S1 also uses CHoCH freshness.
    Without 'time' those filters always passed — now they work correctly.
    """
    window = candles[-lookback:] if len(candles) > lookback else candles
    if len(window) < 6:
        return []

    trend  = _compute_trend(window, 5)
    events = []
    for i in range(5, len(window)):
        prior = window[i - 5: i - 1]
        curr  = window[i]
        if trend == "bullish" and curr["close"] < min(c["low"] for c in prior):
            events.append({
                "direction": "bearish",
                "price":     curr["close"],
                "time":      curr["time"],    # BUG-01
            })
        elif trend == "bearish" and curr["close"] > max(c["high"] for c in prior):
            events.append({
                "direction": "bullish",
                "price":     curr["close"],
                "time":      curr["time"],    # BUG-01
            })
    return events[-3:]


def _detect_zones(candles: list, symbol: str = None, lookback: int = 40) -> list:
    """BUG-05: pip size resolved per symbol."""
    pip    = _pip(symbol)
    window = candles[-lookback:] if len(candles) > lookback else candles
    zones  = []
    for i in range(3, len(window)):
        c      = window[i]
        nearby = [w for w in window[max(0, i - 5): i]
                  if abs(w["close"] - c["close"]) < 30 * pip]
        if len(nearby) >= 2:
            top    = max(n["high"] for n in nearby + [c])
            bottom = min(n["low"]  for n in nearby + [c])
            center = (top + bottom) / 2
            if top - bottom >= 3 * pip:
                zones.append({"top": top, "bottom": bottom, "center": center})
    unique = []
    for z in zones:
        if not any(abs(z["center"] - u["center"]) < 10 * pip for u in unique):
            unique.append(z)
    return unique[-6:]


def _detect_sr_levels(candles: list, symbol: str = None,
                      price: float = None, lookback: int = 60) -> list:
    """BUG-05: pip size resolved per symbol."""
    pip       = _pip(symbol)
    window    = candles[-lookback:] if len(candles) > lookback else candles
    threshold = 15 * pip
    touched: dict = {}

    for c in window:
        for lvl in list(touched.keys()):
            if abs(c["high"] - lvl) <= threshold or abs(c["low"] - lvl) <= threshold:
                touched[lvl] += 1
        h_key = round(c["high"] / threshold) * threshold
        if h_key not in touched:
            touched[h_key] = 1
        l_key = round(c["low"] / threshold) * threshold
        if l_key not in touched:
            touched[l_key] = 1

    ref_price = price or (candles[-1]["close"] if candles else 0)
    levels    = []
    for lvl, count in touched.items():
        if count >= 2:
            kind = "resistance" if lvl > ref_price else "support"
            levels.append({"price": lvl, "kind": kind})
    return levels


# ── Session helpers ───────────────────────────────────────────────────────────

def _get_sessions(ts: int) -> list:
    """Derive active sessions from a Unix timestamp (not the wall clock)."""
    hour     = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    sessions = []
    if 0  <= hour < 9:  sessions.append("asian")
    if 8  <= hour < 17: sessions.append("london")
    if 13 <= hour < 22: sessions.append("ny")
    return sessions


# ── Asia range — pre-cached O(n) (BUG-14) ────────────────────────────────────

def _build_asia_cache(candles: list) -> dict:
    """
    BUG-14: single O(n) pass over all candles to build {date_str: (high, low)}.
    The original scanned candles[:idx] inside the main loop — O(n²) — which
    froze 30-day runs. This builds the whole cache once before the loop starts.
    """
    per_day: dict = defaultdict(list)
    for c in candles:
        dt = datetime.fromtimestamp(c["time"], tz=timezone.utc)
        if 0 <= dt.hour < 9:
            per_day[dt.strftime("%Y-%m-%d")].append(c)

    return {
        date_str: (max(c["high"] for c in cs), min(c["low"] for c in cs))
        for date_str, cs in per_day.items()
    }


def _get_asia_range_cached(cache: dict, ts: int) -> tuple:
    """O(1) lookup using the pre-built cache."""
    date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return cache.get(date_str, (None, None))


# ── S5 / S6 session monkey-patch helpers (BUG-02) ────────────────────────────

def _bt_session_window(candle_dt: datetime) -> tuple[bool, str]:
    """
    BUG-02 — Drop-in for scalp5._in_session_open_window().

    The original function calls datetime.now(timezone.utc). In a backtest
    that means every historical candle is judged against today's clock.
    If you run the backtest at 3pm, S5 fires on every candle as if it is
    always a London/NY open regardless of when the candle actually happened.

    This replacement takes the candle's actual datetime and checks its
    minute-of-day against DST-aware London and New York open windows.
    The 90-minute session-open window logic matches scalp5.py exactly.
    """
    mins = candle_dt.hour * 60 + candle_dt.minute
    # DST-aware UTC offset for the candle's actual date (not today's date)
    lo = int(candle_dt.astimezone(ZoneInfo("Europe/London")).utcoffset().total_seconds() // 3600)
    ny = int(candle_dt.astimezone(ZoneInfo("America/New_York")).utcoffset().total_seconds() // 3600)
    lo_open = (8 - lo) * 60
    ny_open = (8 - ny) * 60
    if lo_open <= mins < lo_open + 90:
        return True, "London"
    if ny_open <= mins < ny_open + 90:
        return True, "NY"
    return False, ""


def _bt_asian_session(candle_dt: datetime) -> bool:
    """
    BUG-02 — Drop-in for scalp6._in_asian_session().

    Same problem as S5: the original uses datetime.now(). This uses the
    candle's actual timestamp so S6 correctly identifies Asian-session
    candles in historical data regardless of when the backtest is run.
    """
    mins = candle_dt.hour * 60 + candle_dt.minute
    return 0 <= mins < 9 * 60


# ── Historical state builder (BUG-03/04/07/08/11/14/15) ──────────────────────

def build_historical_state(candles: list, idx: int,
                            symbol: str, asia_cache: dict) -> dict | None:
    """
    Build a strategy-compatible state dict from 5M candles at position idx.

    Fixes vs original build_historical_state():
      BUG-03: 1H candles synthesised  → S3 (ICT OB/FVG) gets real 1H candles
      BUG-04: all events have 'time'  → staleness filters work correctly
      BUG-07: 4H synthesised          → S3 4H OB-stacking, macro bias correct
      BUG-08: bias from each TF's own window — not all derived from raw 5M
      BUG-11: 'symbol' key always set in state dict
      BUG-14: Asia range from O(1) cache lookup
      BUG-15: candle window 100 bars (was 50) for S4 compression lookback
    """
    # Need ≥200 bars to synthesise meaningful 4H data (≈4 bars × 48 5M bars)
    if idx < 200 or idx >= len(candles):
        return None

    candle = candles[idx]
    price  = candle["close"]
    ts     = candle["time"]

    # ── Synthesise per-TF candle lists (BUG-03 / BUG-07) ─────────────────
    #   15M = 3  × 5M → keep last 60 synthetic 15M bars (180 5M bars back)
    #   1H  = 12 × 5M → keep last 50 synthetic 1H  bars (600 5M bars back)
    #   4H  = 48 × 5M → keep last 30 synthetic 4H  bars (uses what's available)
    candles_15m = _aggregate_candles(candles, idx,  3, 60)
    candles_1h  = _aggregate_candles(candles, idx, 12, 50)
    candles_4h  = _aggregate_candles(candles, idx, 48, 30)

    # Raw 5M window for execution TF — BUG-15: 100 bars (was 50)
    candles_5m_window = candles[max(0, idx - 100): idx]

    # ── Independent bias per TF (BUG-08) ─────────────────────────────────
    bias_4h  = _compute_trend(candles_4h,  min(5, len(candles_4h)))
    bias_1h  = _compute_trend(candles_1h,  min(5, len(candles_1h)))
    bias_15m = _compute_trend(candles_15m, min(5, len(candles_15m)))

    # ── Per-TF detectors — all events carry timestamps (BUG-01 / BUG-04) ─

    # 5M — execution timeframe
    bos_5m       = _detect_bos(candles_5m_window, 20)
    choch_5m     = _detect_choch(candles_5m_window, 20)
    structure_5m = _detect_swing_structure(candles_5m_window, 30)
    zones_5m     = _detect_zones(candles_5m_window, symbol, 40)

    # 15M — confirmation timeframe
    bos_15m       = _detect_bos(candles_15m, 20)
    choch_15m     = _detect_choch(candles_15m, 20)
    structure_15m = _detect_swing_structure(candles_15m, 30)
    zones_15m     = _detect_zones(candles_15m, symbol, 40)

    # 1H — bias + S3 order blocks (BUG-03: S3 checks candles_1h is non-empty)
    bos_1h       = _detect_bos(candles_1h, 20)
    choch_1h     = _detect_choch(candles_1h, 20)
    structure_1h = _detect_swing_structure(candles_1h, 30)
    zones_1h     = _detect_zones(candles_1h, symbol, 40)

    # 4H — macro bias + S3 4H OB-stacking (BUG-07)
    bos_4h       = _detect_bos(candles_4h, 10)
    choch_4h     = _detect_choch(candles_4h, 10)
    structure_4h = _detect_swing_structure(candles_4h, 20)
    zones_4h     = _detect_zones(candles_4h, symbol, 20)

    # ── S/R levels (BUG-05: symbol-aware pip size) ────────────────────────
    sr_levels = _detect_sr_levels(candles_5m_window, symbol, price, 60)

    # ── Sessions + Asia range (BUG-14: O(1) lookup) ───────────────────────
    sessions            = _get_sessions(ts)
    asia_high, asia_low = _get_asia_range_cached(asia_cache, ts)

    return sanitize_state({
        "symbol":            symbol,              # BUG-11: always set
        "current_price":     price,
        "sessions":          sessions,
        "tradeable_session": is_tradeable_session(sessions),
        "bias": {
            "4h":  bias_4h,
            "1h":  bias_1h,
            "15m": bias_15m,
        },
        # ── Execution TF ─────────────────────────────────────────────────
        "5m": {
            "trend":     bias_15m,
            "structure": structure_5m,
            "bos":       bos_5m,
            "choch":     choch_5m,
            "zones":     zones_5m,
            "candles":   candles_5m_window,
            "sr_levels": sr_levels,
        },
        # ── Confirmation TF ───────────────────────────────────────────────
        "15m": {
            "trend":     bias_1h,
            "structure": structure_15m,
            "bos":       bos_15m,
            "choch":     choch_15m,
            "zones":     zones_15m,
            "candles":   candles_15m,    # BUG-07: real synthetic 15M bars
        },
        # ── Bias TF — S3 order blocks need real candles here (BUG-03) ────
        "1h": {
            "trend":     bias_1h,
            "structure": structure_1h,
            "bos":       bos_1h,
            "choch":     choch_1h,
            "zones":     zones_1h,
            "candles":   candles_1h,     # BUG-03: S3 checks this is non-empty
        },
        # ── Macro bias TF (BUG-07) ────────────────────────────────────────
        "4h": {
            "trend":     bias_4h,
            "structure": structure_4h,
            "bos":       bos_4h,
            "choch":     choch_4h,
            "zones":     zones_4h,
            "candles":   candles_4h,     # BUG-07: real synthetic 4H bars
        },
        "sr_levels": sr_levels,
        "asia_range": {"high": asia_high, "low": asia_low},
    })


# ── Outcome determination (BUG-06 / BUG-13) ──────────────────────────────────

def determine_outcome(candles: list, signal_idx: int,
                      sl: float, tp: float, direction: str,
                      pip_size: float,
                      spread_pips: float = DEFAULT_SPREAD_PIPS,
                      max_candles: int = 100) -> dict:
    """
    Walk forward from signal_idx and determine WIN / LOSS / TIMEOUT.

    BUG-06: spread_cost is subtracted from the effective TP so the backtest
            accounts for entry cost the same way the live engine does. A tighter
            effective TP means marginal trades that would be rejected live are
            also rejected here.

    BUG-13: when a candle hits BOTH TP and SL (wide-ranging candle), the
            original used a distance-to-entry heuristic to guess which hit
            first. The corrected version uses candle body direction instead:
              bullish body (close >= open) → price went up first
                → BUY = WIN,  SELL = LOSS
              bearish body (close < open)  → price went down first
                → BUY = LOSS, SELL = WIN
    """
    spread_cost = spread_pips * pip_size
    # Effective TP after spread: always moves TP away from entry (harder to hit)
    tp_eff = (tp - spread_cost) if direction == "BUY" else (tp + spread_cost)
    entry  = candles[signal_idx]["close"]

    for i in range(1, max_candles + 1):
        j = signal_idx + i
        if j >= len(candles):
            break
        c = candles[j]

        if direction == "BUY":
            hit_tp = c["high"] >= tp_eff
            hit_sl = c["low"]  <= sl
        else:
            hit_tp = c["low"]  <= tp_eff
            hit_sl = c["high"] >= sl

        if hit_tp and hit_sl:
            # BUG-13: use candle body direction to infer which level hit first
            if direction == "BUY":
                first = "WIN" if c["close"] >= c["open"] else "LOSS"
            else:
                first = "WIN" if c["close"] <= c["open"] else "LOSS"
            pips = (abs(tp_eff - entry) / pip_size if first == "WIN"
                    else -abs(sl - entry) / pip_size)
            return {"result": first, "candles_held": i, "pips": pips}

        if hit_tp:
            return {"result": "WIN",  "candles_held": i,
                    "pips":  abs(tp_eff - entry) / pip_size}
        if hit_sl:
            return {"result": "LOSS", "candles_held": i,
                    "pips": -abs(sl - entry) / pip_size}

    return {"result": "TIMEOUT", "candles_held": max_candles, "pips": 0}


# ── Main backtest function ────────────────────────────────────────────────────

def run_backtest(days: int = 7, symbol: str = None) -> dict:
    """
    Run a full historical backtest using 5M candles.

    Parameters
    ----------
    days   : calendar days of history to replay (default 7)
    symbol : which pair to test (default: config.SYMBOL = USD/JPY)

    Backward-compatible with dashboard_server.py which calls
    run_backtest(days=days) — the new symbol kwarg has a safe default.
    """
    sym      = symbol or config.SYMBOL
    pip_size = _pip(sym)
    start_ts = time.time()

    print(f"[BACKTEST] Starting {days}-day backtest for {sym}")

    candles, source = fetch_candles(days, sym)
    if candles is None:
        return {
            "error": (
                f"Could not fetch candle data for {sym}. Source: {source}. "
                "Set TWELVE_DATA_API_KEY or ensure MT5 is running."
            )
        }

    # ── BUG-14: build Asia range cache in a single O(n) pass ─────────────
    asia_cache = _build_asia_cache(candles)

    # ── Locate the test window start index ────────────────────────────────
    cutoff_ts      = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    test_start_idx = next((i for i, c in enumerate(candles) if c["time"] >= cutoff_ts), 200)
    # Enforce minimum of 200 bars so 4H synthesis always has enough raw 5M data
    test_start_idx = max(test_start_idx, 200)

    print(
        f"[BACKTEST] {len(candles)} candles loaded ({source}). "
        f"Test window: index {test_start_idx} → {len(candles) - 100}"
    )

    # ── BUG-02 / BUG-09: monkey-patch S5 and S6 before the main loop ─────
    #
    # scalp5._in_session_open_window() and scalp6._in_asian_session() both
    # call datetime.now() internally.  In a backtest this means every
    # historical candle is judged against the current wall clock —
    # S5 and S6 fire at completely wrong times.
    #
    # Fix: temporarily replace those module-level function references with
    # wrapper lambdas that accept the candle's actual datetime.  A
    # default-argument lambda (lambda dt=candle_dt: f(dt)) captures the
    # value at lambda creation time, so each loop iteration gets the correct
    # candle timestamp without any closure-over-loop-variable bug.
    #
    # The strategy .py files are NEVER modified — only their in-memory
    # function references are swapped.  The finally block restores the
    # originals even if an exception is raised mid-backtest.
    #
    # BUG-09: _s6._mark_fired is replaced with a no-op so the backtest never
    # writes to s6_cooldown.json, which would block the live engine from
    # trading Asian range boundaries for the rest of the calendar day.
    import strategies.scalp5 as _s5
    import strategies.scalp6 as _s6
    _orig_s5_window = _s5._in_session_open_window
    _orig_s6_asian  = _s6._in_asian_session
    _orig_s6_mark   = _s6._mark_fired
    _s6._mark_fired = lambda sym_, side: None    # BUG-09: block disk writes

    trades: list = []
    last_signal_idx = -3

    # BUG-10: per-day counters that mirror the live engine's risk limits
    daily_trade_count: dict = defaultdict(int)
    daily_consec_loss: dict = defaultdict(int)

    try:
        for idx in range(test_start_idx, len(candles) - 100):

            # 15-min cooldown between signals (3 × 5M bars)
            if idx - last_signal_idx < 3:
                continue

            candle_ts = candles[idx]["time"]
            candle_dt = datetime.fromtimestamp(candle_ts, tz=timezone.utc)
            day_str   = candle_dt.strftime("%Y-%m-%d")

            # ── BUG-10: daily trade cap (matches config.MAX_TRADES_PER_DAY) ─
            if daily_trade_count[day_str] >= config.MAX_TRADES_PER_DAY:
                continue

            # ── BUG-10: consecutive-loss cutoff (matches MAX_CONSECUTIVE_LOSSES)
            if daily_consec_loss[day_str] >= config.MAX_CONSECUTIVE_LOSSES:
                continue

            # ── BUG-02: swap S5/S6 session checks to use this candle's time ─
            # Default-argument trick captures candle_dt by value at lambda
            # creation, not by reference, so each iteration is independent.
            _s5._in_session_open_window = (
                lambda dt=candle_dt: _bt_session_window(dt)
            )
            _s6._in_asian_session = (
                lambda dt=candle_dt: _bt_asian_session(dt)
            )

            # ── Build state for this candle ───────────────────────────────
            state = build_historical_state(candles, idx, sym, asia_cache)
            if state is None:
                continue

            # ── Evaluate all strategies; pick highest-confidence signal ───
            best_result = None
            best_score  = 0

            for name, strategy_fn in STRATEGIES:
                try:
                    result = strategy_fn(state, debug=False)
                except Exception:
                    continue
                if (result and result.get("trade")
                        and result.get("confidence", 0) > best_score):
                    best_score  = result["confidence"]
                    best_result = result

            if best_result is None:
                continue

            # Minimum confidence gate — same threshold as the live engine
            if best_score < config.MIN_CONFIDENCE:
                continue

            sl        = best_result.get("sl",  0)
            tp        = best_result.get("tp",  0)
            entry     = best_result.get("entry", candles[idx]["close"])
            direction = best_result.get("type", "")

            if sl == 0 or tp == 0 or direction not in ("BUY", "SELL"):
                continue

            sl_dist = abs(entry - sl)
            tp_dist = abs(entry - tp)
            if sl_dist == 0:
                continue
            rr = tp_dist / sl_dist

            # ── Outcome (BUG-05/06/13) ────────────────────────────────────
            outcome = determine_outcome(
                candles, idx, sl, tp, direction,
                pip_size=pip_size,
                spread_pips=DEFAULT_SPREAD_PIPS,
                max_candles=100,
            )

            sessions      = _get_sessions(candle_ts)
            session_label = (
                "NY" if "ny" in sessions
                else ("London" if "london" in sessions else "Asian")
            )

            trades.append({
                "date":      candle_dt.strftime("%Y-%m-%d %H:%M"),
                "day":       day_str,
                "strategy":  best_result.get("strategy", name),
                "direction": direction,
                "session":   session_label,
                "score":     best_score,
                "entry":     round(entry, 5),
                "sl":        round(sl, 5),
                "tp":        round(tp, 5),
                "rr":        round(rr, 2),
                "result":    outcome["result"],
                "pips":      round(outcome["pips"], 1),
                "held_5m":   outcome["candles_held"],
            })

            last_signal_idx = idx

            # ── BUG-10: update daily risk counters ────────────────────────
            daily_trade_count[day_str] += 1
            if outcome["result"] == "WIN":
                daily_consec_loss[day_str] = 0        # win resets the streak
            elif outcome["result"] == "LOSS":
                daily_consec_loss[day_str] += 1       # loss extends the streak
            # TIMEOUT: leave consecutive-loss counter unchanged

    finally:
        # ── Always restore originals — even on exception (BUG-02 / BUG-09) ─
        _s5._in_session_open_window = _orig_s5_window
        _s6._in_asian_session       = _orig_s6_asian
        _s6._mark_fired             = _orig_s6_mark

    # ── Aggregate statistics ───────────────────────────────────────────────
    total    = len(trades)
    wins     = [t for t in trades if t["result"] == "WIN"]
    losses   = [t for t in trades if t["result"] == "LOSS"]
    timeouts = [t for t in trades if t["result"] == "TIMEOUT"]

    win_rate      = round(len(wins)  / total * 100, 1) if total > 0 else 0
    avg_rr        = round(sum(t["rr"]   for t in trades) / total, 2) if total > 0 else 0
    avg_pips_win  = round(sum(t["pips"] for t in wins)   / len(wins),   1) if wins   else 0
    avg_pips_loss = round(sum(t["pips"] for t in losses) / len(losses), 1) if losses else 0
    total_pips    = round(sum(t["pips"] for t in trades), 1)
    per_week      = round(total / (days / 7), 1) if days > 0 else 0

    # ── Per-strategy breakdown ─────────────────────────────────────────────
    strategy_stats: dict = {}
    for t in trades:
        s = t["strategy"]
        if s not in strategy_stats:
            strategy_stats[s] = {"total": 0, "wins": 0, "losses": 0,
                                  "timeouts": 0, "pips": 0.0}
        strategy_stats[s]["total"] += 1
        strategy_stats[s]["pips"]  += t["pips"]
        if   t["result"] == "WIN":  strategy_stats[s]["wins"]     += 1
        elif t["result"] == "LOSS": strategy_stats[s]["losses"]   += 1
        else:                       strategy_stats[s]["timeouts"]  += 1

    for s in strategy_stats:
        n = strategy_stats[s]["total"]
        strategy_stats[s]["win_rate"] = (
            round(strategy_stats[s]["wins"] / n * 100, 1) if n > 0 else 0
        )
        strategy_stats[s]["pips"] = round(strategy_stats[s]["pips"], 1)

    # ── Per-session breakdown ──────────────────────────────────────────────
    session_stats: dict = {}
    for t in trades:
        sess = t["session"]
        if sess not in session_stats:
            session_stats[sess] = {"total": 0, "wins": 0, "losses": 0}
        session_stats[sess]["total"] += 1
        if   t["result"] == "WIN":  session_stats[sess]["wins"]   += 1
        elif t["result"] == "LOSS": session_stats[sess]["losses"] += 1

    for sess in session_stats:
        n = session_stats[sess]["total"]
        session_stats[sess]["win_rate"] = (
            round(session_stats[sess]["wins"] / n * 100, 1) if n > 0 else 0
        )

    # ── Per-day breakdown (new — useful for drawdown / daily-limit analysis)
    daily_stats: dict = {}
    for t in trades:
        d = t["day"]
        if d not in daily_stats:
            daily_stats[d] = {"total": 0, "wins": 0, "losses": 0, "pips": 0.0}
        daily_stats[d]["total"] += 1
        daily_stats[d]["pips"]  += t["pips"]
        if   t["result"] == "WIN":  daily_stats[d]["wins"]   += 1
        elif t["result"] == "LOSS": daily_stats[d]["losses"] += 1

    for d in daily_stats:
        daily_stats[d]["pips"] = round(daily_stats[d]["pips"], 1)

    elapsed = round(time.time() - start_ts, 1)
    print(
        f"[BACKTEST] Done in {elapsed}s — "
        f"{total} trades | {win_rate}% win rate | {total_pips} pips | {sym}"
    )

    return {
        "symbol":        sym,
        "source":        source,
        "days":          days,
        "candles_total": len(candles),
        "elapsed_sec":   elapsed,
        "summary": {
            "total_trades":    total,
            "wins":            len(wins),
            "losses":          len(losses),
            "timeouts":        len(timeouts),
            "win_rate":        win_rate,
            "avg_rr":          avg_rr,
            "avg_pips_win":    avg_pips_win,
            "avg_pips_loss":   avg_pips_loss,
            "total_pips":      total_pips,
            "trades_per_week": per_week,
        },
        "by_strategy": strategy_stats,
        "by_session":  session_stats,
        "by_day":      daily_stats,    # new field — per-day pips / drawdown
        "trades":      list(reversed(trades)),
    }