"""
STRUCT.ai Scalping Engine — Backtester

Fetches historical USDJPY 5M/1M candles and replays all 5 scalping strategies
candle by candle to calculate win rate, average RR, and per-strategy performance.

Data source priority:
  1. MetaTrader5 Python library (Windows only — most accurate, same broker feed)
  2. Twelve Data REST API (cross-platform fallback, 5M candles)

Zero changes to existing strategies, risk manager, or live engine logic.
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config
from state import sanitize_state, is_tradeable_session
from strategies import STRATEGIES

DATA_DIR = Path(__file__).parent / "backtest_data"
DATA_DIR.mkdir(exist_ok=True)

TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"

PIP    = config.PIP_SIZE
SL_BUF = config.SL_BUFFER_PIPS * PIP


# ── MT5 fetch (Windows only) ──────────────────────────────────────────────────

def _fetch_mt5(interval: str = "5m", count: int = 5000) -> list | None:
    """Fetch candles from MT5. interval: '1m', '5m'."""
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
        tf = tf_map.get(interval, mt5.TIMEFRAME_M5)

        rates = mt5.copy_rates_from_pos(config.MT5_SYMBOL, tf, 0, count)
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


# ── Twelve Data fetch (cross-platform fallback) ───────────────────────────────

def _fetch_twelvedata(interval: str = "5min", count: int = 5000) -> list | None:
    """Twelve Data interval format: '1min', '5min', '15min', '1h'."""
    if not TWELVE_DATA_KEY:
        return None
    try:
        import requests
        resp = requests.get(TWELVE_DATA_URL, params={
            "symbol":     "USD/JPY",
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
            dt = datetime.strptime(v["datetime"], fmt).replace(tzinfo=timezone.utc)
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


def fetch_candles(days: int = 7) -> tuple:
    """
    Returns (candles list, source name str) or (None, error str).

    Scalping backtest uses 5M candles:
      - days=7  →  ~2016 candles (7d × 24h × 12 5m-candles)
      - days=30 →  ~8640 candles
    Requests extra candles for the lookback window.
    """
    # 5M candles per day ≈ 288 (24h × 12 per hour)
    needed = days * 288 + 500

    candles = _fetch_mt5("5m", min(needed, 10000))
    if candles:
        return candles, "MT5 (5M)"

    candles = _fetch_twelvedata("5min", min(needed, 5000))
    if candles:
        return candles, "Twelve Data (5M)"

    return None, "unavailable"


# ── State builders ────────────────────────────────────────────────────────────

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


def _compute_bias_4h(candles_5m: list, idx: int) -> str:
    """4H bias from last 48 5M candles (= 4H)."""
    window = candles_5m[max(0, idx - 48): idx]
    return _compute_trend(window, min(5, len(window)))


def _compute_bias_1h(candles_5m: list, idx: int) -> str:
    """1H bias from last 12 5M candles."""
    window = candles_5m[max(0, idx - 12): idx]
    return _compute_trend(window, min(5, len(window)))


def _compute_bias_15m(candles_5m: list, idx: int) -> str:
    """15M bias from last 3 5M candles."""
    window = candles_5m[max(0, idx - 3): idx]
    return _compute_trend(window, min(3, len(window)))


def _detect_swing_structure(candles: list, idx: int, lookback: int = 30) -> list:
    window = candles[max(0, idx - lookback): idx]
    structure = []
    prev_high = None
    prev_low  = None

    for i in range(2, len(window)):
        c_prev = window[i - 2]
        c_mid  = window[i - 1]
        c_curr = window[i]

        if c_mid["high"] > c_prev["high"] and c_mid["high"] > c_curr["high"]:
            label = "HH" if (prev_high is None or c_mid["high"] > prev_high) else "LH"
            structure.append({"label": label, "price": c_mid["high"], "kind": "high"})
            prev_high = c_mid["high"]

        if c_mid["low"] < c_prev["low"] and c_mid["low"] < c_curr["low"]:
            label = "HL" if (prev_low is None or c_mid["low"] > prev_low) else "LL"
            structure.append({"label": label, "price": c_mid["low"], "kind": "low"})
            prev_low = c_mid["low"]

    return structure


def _detect_bos(candles: list, idx: int, lookback: int = 20) -> list:
    window = candles[max(0, idx - lookback): idx]
    if len(window) < 6:
        return []
    events = []
    for i in range(5, len(window)):
        prior      = window[i - 5: i - 1]
        swing_high = max(c["high"] for c in prior)
        swing_low  = min(c["low"]  for c in prior)
        curr = window[i]
        if curr["close"] > swing_high:
            events.append({"direction": "bullish", "price": curr["close"]})
        elif curr["close"] < swing_low:
            events.append({"direction": "bearish", "price": curr["close"]})
    return events[-5:]


def _detect_choch(candles: list, idx: int, lookback: int = 20) -> list:
    window = candles[max(0, idx - lookback): idx]
    if len(window) < 6:
        return []
    trend  = _compute_trend(window, 5)
    events = []
    for i in range(5, len(window)):
        prior = window[i - 5: i - 1]
        curr  = window[i]
        if trend == "bullish" and curr["close"] < min(c["low"] for c in prior):
            events.append({"direction": "bearish"})
        elif trend == "bearish" and curr["close"] > max(c["high"] for c in prior):
            events.append({"direction": "bullish"})
    return events[-3:]


def _detect_zones(candles: list, idx: int, lookback: int = 40) -> list:
    window = candles[max(0, idx - lookback): idx]
    zones  = []
    for i in range(3, len(window)):
        c = window[i]
        nearby = [w for w in window[max(0, i - 5): i]
                  if abs(w["close"] - c["close"]) < 30 * PIP]
        if len(nearby) >= 2:
            top    = max(n["high"] for n in nearby + [c])
            bottom = min(n["low"]  for n in nearby + [c])
            center = (top + bottom) / 2
            if top - bottom >= 3 * PIP:
                zones.append({"top": top, "bottom": bottom, "center": center})
    unique = []
    for z in zones:
        if not any(abs(z["center"] - u["center"]) < 10 * PIP for u in unique):
            unique.append(z)
    return unique[-6:]


def _detect_sr_levels(candles: list, idx: int, lookback: int = 60) -> list:
    window    = candles[max(0, idx - lookback): idx]
    levels    = []
    touched   = {}
    threshold = 15 * PIP

    for c in window:
        for lvl in list(touched.keys()):
            if abs(c["high"] - lvl) <= threshold:
                touched[lvl] += 1
            elif abs(c["low"] - lvl) <= threshold:
                touched[lvl] += 1
        h_key = round(c["high"] / threshold) * threshold
        if h_key not in touched:
            touched[h_key] = 1
        l_key = round(c["low"] / threshold) * threshold
        if l_key not in touched:
            touched[l_key] = 1

    price = candles[idx]["close"] if idx < len(candles) else candles[-1]["close"]
    for lvl, count in touched.items():
        if count >= 2:
            kind = "resistance" if lvl > price else "support"
            levels.append({"price": lvl, "kind": kind})
    return levels


def _get_sessions(ts: int) -> list:
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    sessions = []
    if 0 <= hour < 9:
        sessions.append("asian")
    if 8 <= hour < 17:
        sessions.append("london")
    if 13 <= hour < 22:
        sessions.append("ny")
    return sessions


def _get_asia_range(candles: list, idx: int) -> tuple:
    if idx >= len(candles):
        return None, None
    ts   = candles[idx]["time"]
    date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
    asia = [
        c for c in candles[:idx]
        if datetime.fromtimestamp(c["time"], tz=timezone.utc).date() == date
        and 0 <= datetime.fromtimestamp(c["time"], tz=timezone.utc).hour < 9
    ]
    if not asia:
        return None, None
    return max(c["high"] for c in asia), min(c["low"] for c in asia)


def build_historical_state(candles: list, idx: int) -> dict | None:
    """Build a strategy-compatible state dict from 5M historical candles at index idx."""
    if idx < 50 or idx >= len(candles):
        return None

    candle = candles[idx]
    price  = candle["close"]
    ts     = candle["time"]

    bias_4h  = _compute_bias_4h(candles, idx)
    bias_1h  = _compute_bias_1h(candles, idx)
    bias_15m = _compute_bias_15m(candles, idx)

    structure = _detect_swing_structure(candles, idx, 30)
    bos       = _detect_bos(candles, idx, 20)
    choch     = _detect_choch(candles, idx, 20)
    zones     = _detect_zones(candles, idx, 40)
    sr_levels = _detect_sr_levels(candles, idx, 60)
    sessions  = _get_sessions(ts)
    asia_high, asia_low = _get_asia_range(candles, idx)

    candles_window = candles[max(0, idx - 50): idx]

    return sanitize_state({
        "current_price":     price,
        "sessions":          sessions,
        "tradeable_session": is_tradeable_session(sessions),
        "bias": {
            "4h":  bias_4h,
            "1h":  bias_1h,
            "15m": bias_15m,
        },
        "1m": {
            "trend":     bias_15m,
            "structure": structure,
            "bos":       bos,
            "choch":     choch,
            "zones":     zones,
            "candles":   candles_window,
            "sr_levels": sr_levels,
        },
        "5m": {
            "trend":     bias_15m,
            "structure": structure,
            "bos":       bos,
            "choch":     choch,
            "zones":     zones,
            "candles":   candles_window,
            "sr_levels": sr_levels,
        },
        "15m": {
            "trend":     bias_1h,
            "structure": structure,
            "bos":       bos,
            "choch":     choch,
            "zones":     zones,
        },
        "1h": {
            "trend":     bias_1h,
            "structure": structure,
            "bos":       bos,
            "choch":     choch,
            "zones":     zones,
        },
        "sr_levels": sr_levels,
        "asia_range": {"high": asia_high, "low": asia_low},
    })


# ── Outcome determination ─────────────────────────────────────────────────────

def determine_outcome(candles: list, signal_idx: int, sl: float, tp: float,
                      direction: str, max_candles: int = 100) -> dict:
    """
    Look at candles after signal fires (5M candles, max_candles = 100 = ~8H).
    Returns {"result": "WIN"/"LOSS"/"TIMEOUT", "candles_held": int, "pips": float}
    """
    for i in range(1, max_candles + 1):
        j = signal_idx + i
        if j >= len(candles):
            break
        c = candles[j]

        if direction == "BUY":
            if c["high"] >= tp and c["low"] <= sl:
                first = "WIN" if (tp - candles[signal_idx]["close"]) <= (candles[signal_idx]["close"] - sl) else "LOSS"
                return {"result": first, "candles_held": i,
                        "pips": abs(tp - candles[signal_idx]["close"]) / PIP if first == "WIN"
                                else -abs(sl - candles[signal_idx]["close"]) / PIP}
            if c["high"] >= tp:
                return {"result": "WIN",  "candles_held": i,
                        "pips": abs(tp - candles[signal_idx]["close"]) / PIP}
            if c["low"] <= sl:
                return {"result": "LOSS", "candles_held": i,
                        "pips": -abs(sl - candles[signal_idx]["close"]) / PIP}

        else:  # SELL
            if c["low"] <= tp and c["high"] >= sl:
                first = "WIN" if (candles[signal_idx]["close"] - tp) <= (sl - candles[signal_idx]["close"]) else "LOSS"
                return {"result": first, "candles_held": i,
                        "pips": abs(candles[signal_idx]["close"] - tp) / PIP if first == "WIN"
                                else -abs(sl - candles[signal_idx]["close"]) / PIP}
            if c["low"] <= tp:
                return {"result": "WIN",  "candles_held": i,
                        "pips": abs(candles[signal_idx]["close"] - tp) / PIP}
            if c["high"] >= sl:
                return {"result": "LOSS", "candles_held": i,
                        "pips": -abs(sl - candles[signal_idx]["close"]) / PIP}

    return {"result": "TIMEOUT", "candles_held": max_candles, "pips": 0}


# ── Main backtest function ────────────────────────────────────────────────────

def run_backtest(days: int = 7) -> dict:
    """
    Run full historical backtest using 5M candles.
    Returns results dict ready to send to the dashboard.
    """
    start_ts = time.time()

    candles, source = fetch_candles(days)
    if candles is None:
        return {"error": f"Could not fetch candle data. Source: {source}. "
                         "Make sure TWELVE_DATA_API_KEY is set or MT5 is running."}

    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    test_start_idx = next((i for i, c in enumerate(candles) if c["time"] >= cutoff_ts), 50)
    test_start_idx = max(test_start_idx, 50)

    trades = []
    last_signal_idx = -3  # Scalping: skip 3 candles (~15 min) after a signal

    for idx in range(test_start_idx, len(candles) - 100):
        if idx - last_signal_idx < 3:
            continue

        state = build_historical_state(candles, idx)
        if state is None:
            continue

        best_result = None
        best_score  = 0

        for name, strategy_fn in STRATEGIES:
            try:
                result = strategy_fn(state, debug=False)
            except Exception:
                continue

            if result and result.get("trade") and result.get("confidence", 0) > best_score:
                best_score  = result["confidence"]
                best_result = result

        if best_result is None:
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

        outcome = determine_outcome(candles, idx, sl, tp, direction, max_candles=100)

        ts  = candles[idx]["time"]
        dt  = datetime.fromtimestamp(ts, tz=timezone.utc)
        sessions = _get_sessions(ts)
        session_label = "NY" if "ny" in sessions else ("London" if "london" in sessions else "Asian")

        trades.append({
            "date":      dt.strftime("%Y-%m-%d %H:%M"),
            "strategy":  best_result.get("strategy", name),
            "direction": direction,
            "session":   session_label,
            "score":     best_result.get("confidence", 0),
            "entry":     round(entry, 3),
            "sl":        round(sl, 3),
            "tp":        round(tp, 3),
            "rr":        round(rr, 2),
            "result":    outcome["result"],
            "pips":      round(outcome["pips"], 1),
            "held_5m":   outcome["candles_held"],
        })
        last_signal_idx = idx

    # ── Aggregate stats ────────────────────────────────────────────────────────
    total    = len(trades)
    wins     = [t for t in trades if t["result"] == "WIN"]
    losses   = [t for t in trades if t["result"] == "LOSS"]
    timeouts = [t for t in trades if t["result"] == "TIMEOUT"]

    win_rate      = round(len(wins) / total * 100, 1) if total > 0 else 0
    avg_rr        = round(sum(t["rr"] for t in trades) / total, 2) if total > 0 else 0
    avg_pips_win  = round(sum(t["pips"] for t in wins)   / len(wins),   1) if wins   else 0
    avg_pips_loss = round(sum(t["pips"] for t in losses) / len(losses), 1) if losses else 0
    total_pips    = round(sum(t["pips"] for t in trades), 1)
    per_week      = round(total / (days / 7), 1) if days > 0 else 0

    strategy_stats = {}
    for t in trades:
        s = t["strategy"]
        if s not in strategy_stats:
            strategy_stats[s] = {"total": 0, "wins": 0, "losses": 0, "pips": 0}
        strategy_stats[s]["total"]  += 1
        strategy_stats[s]["pips"]   += t["pips"]
        if t["result"] == "WIN":
            strategy_stats[s]["wins"]   += 1
        elif t["result"] == "LOSS":
            strategy_stats[s]["losses"] += 1
    for s in strategy_stats:
        n = strategy_stats[s]["total"]
        strategy_stats[s]["win_rate"] = round(strategy_stats[s]["wins"] / n * 100, 1) if n > 0 else 0
        strategy_stats[s]["pips"]     = round(strategy_stats[s]["pips"], 1)

    session_stats = {}
    for t in trades:
        sess = t["session"]
        if sess not in session_stats:
            session_stats[sess] = {"total": 0, "wins": 0, "losses": 0}
        session_stats[sess]["total"] += 1
        if t["result"] == "WIN":
            session_stats[sess]["wins"]   += 1
        elif t["result"] == "LOSS":
            session_stats[sess]["losses"] += 1
    for sess in session_stats:
        n = session_stats[sess]["total"]
        session_stats[sess]["win_rate"] = round(session_stats[sess]["wins"] / n * 100, 1) if n > 0 else 0

    elapsed = round(time.time() - start_ts, 1)

    return {
        "source":         source,
        "days":           days,
        "candles_total":  len(candles),
        "elapsed_sec":    elapsed,
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
        "trades":      list(reversed(trades)),
    }
