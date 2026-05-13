"""
Strategy 2 — Liquidity Sweep Reversal Scalping
===============================================
Exploit liquidity grabs (fake breakouts) where price sweeps previous
highs/lows, traps traders, then reverses.

Session 2 fixes applied:
  - 12h staleness guard on sweep events
  - Minimum recovery raised to 5 pips (was 3)
  - 1h freshness check on 5M reversal confirmation
  - Sweep selection: time-based tie-break (most recent wins)
"""

import sys, os, math, time as _time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


def _best_sweep(items: list, key: str, value: str, max_age_secs: int) -> dict | None:
    """Return the most recent item where item[key]==value that is not stale."""
    now = int(_time.time())
    candidates = [
        item for item in items
        if isinstance(item, dict)
        and item.get(key) == value
        and (now - item.get("time", 0)) <= max_age_secs
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.get("time", 0))


def check(state: dict, debug: bool = False) -> dict | None:
    if not isinstance(state, dict):
        return None

    bias  = state.get("bias", {})
    price = state.get("current_price")
    s5m   = state.get("5m", {})
    s15m  = state.get("15m", {})

    if not price or not isinstance(price, (int, float)) or not math.isfinite(price):
        return None

    # ── Step 1: Market condition — avoid strong trends ────────────────────
    b4h = bias.get("4h", "neutral")
    b1h = bias.get("1h", "neutral")

    strongly_trending = (b4h == b1h) and b4h not in ("neutral",) and b4h != ""
    slightly_trending = (b4h not in ("neutral", "") or b1h not in ("neutral", "")) and not strongly_trending

    if strongly_trending:
        if debug: print("    [S2] skip: market strongly trending — use S1")
        return None

    market_score = 15 if not slightly_trending else 5

    # ── Step 2: Sweep detection on 15M with 12h staleness guard ──────────
    SWEEP_MAX_AGE = 12 * 3600   # 12 hours

    bos_15m   = s15m.get("bos", [])
    choch_15m = s15m.get("choch", [])

    bearish_choch = _best_sweep(choch_15m, "direction", "bearish", SWEEP_MAX_AGE)
    bearish_bos   = _best_sweep(bos_15m,   "direction", "bearish", SWEEP_MAX_AGE)
    bullish_choch = _best_sweep(choch_15m, "direction", "bullish", SWEEP_MAX_AGE)
    bullish_bos   = _best_sweep(bos_15m,   "direction", "bullish", SWEEP_MAX_AGE)

    # Pick best sweep for each direction: CHOCH preferred, but if BOS is newer use it
    def _pick(choch_item, bos_item):
        if choch_item and bos_item:
            # If BOS is significantly more recent (>12h newer), use BOS instead
            return choch_item if choch_item.get("time", 0) >= bos_item.get("time", 0) - 12 * 3600 else bos_item
        return choch_item or bos_item

    buy_sweep_item  = _pick(bearish_choch, bearish_bos)
    sell_sweep_item = _pick(bullish_choch, bullish_bos)

    buy_sweep_price  = buy_sweep_item.get("price")  if buy_sweep_item  else None
    sell_sweep_price = sell_sweep_item.get("price") if sell_sweep_item else None

    buy_sweep_score  = (25 if buy_sweep_item  and buy_sweep_item.get("type",  "BOS") == "CHOCH" else 10) if buy_sweep_item  else 0
    sell_sweep_score = (25 if sell_sweep_item and sell_sweep_item.get("type", "BOS") == "CHOCH" else 10) if sell_sweep_item else 0

    # Fallback: infer score from which list it came from
    if buy_sweep_item and buy_sweep_score == 10:
        buy_sweep_score  = 25 if buy_sweep_item  in choch_15m else 10
    if sell_sweep_item and sell_sweep_score == 10:
        sell_sweep_score = 25 if sell_sweep_item in choch_15m else 10

    # ── Step 3: Verify reversal — 5 pip minimum recovery ─────────────────
    pip          = config.get_symbol_cfg(state.get("symbol"))["pip_size"]
    near_pips    = config.NEAR_LEVEL_PIPS
    min_recovery = config.MIN_SWEEP_RECOVERY_PIPS * pip

    direction   = None
    trade_type  = None
    sweep_score = 0
    sweep_level = None

    if buy_sweep_price is not None and (price - buy_sweep_price) >= min_recovery:
        direction   = "bullish"
        trade_type  = "BUY"
        sweep_score = buy_sweep_score
        sweep_level = buy_sweep_price

    if sell_sweep_price is not None and (sell_sweep_price - price) >= min_recovery:
        if direction is None or sell_sweep_score > sweep_score:
            direction   = "bearish"
            trade_type  = "SELL"
            sweep_score = sell_sweep_score
            sweep_level = sell_sweep_price

    if direction is None:
        if debug: print("    [S2] skip: no valid sweep with ≥5p recovery")
        return None

    # ── Step 4: 5M reversal confirmation — with 1h freshness check ───────
    now_sec  = int(_time.time())
    bos_5m   = s5m.get("bos", [])
    choch_5m = s5m.get("choch", [])

    conf_choch = next(
        (c for c in sorted(choch_5m, key=lambda x: x.get("time", 0), reverse=True)
         if isinstance(c, dict) and c.get("direction") == direction
         and (now_sec - c.get("time", 0)) <= 3600), None
    )
    conf_bos = next(
        (b for b in sorted(bos_5m, key=lambda x: x.get("time", 0), reverse=True)
         if isinstance(b, dict) and b.get("direction") == direction
         and (now_sec - b.get("time", 0)) <= 3600), None
    )

    if conf_choch:
        reversal_score = 25
    elif conf_bos:
        reversal_score = 10
    else:
        if debug: print(f"    [S2] skip: no {direction} CHoCH/BOS on 5M within 1h")
        return None

    # ── Step 5: Entry precision ───────────────────────────────────────────
    dist_from_sweep = abs(price - sweep_level)
    dist_pips_sw    = dist_from_sweep / pip

    if dist_pips_sw > 50:
        if debug: print(f"    [S2] skip: {dist_pips_sw:.1f}p from sweep (>50p)")
        return None

    if dist_pips_sw <= 5:
        precision_score = 15
    elif dist_pips_sw <= 15:
        precision_score = 10
    else:
        precision_score = 5

    # ── Step 6: Zone confluence ───────────────────────────────────────────
    zones_5m  = s5m.get("zones") or []
    zones_15m = s15m.get("zones") or []
    if not isinstance(zones_5m,  list): zones_5m  = []
    if not isinstance(zones_15m, list): zones_15m = []
    threshold = near_pips * pip

    zone_ok = False
    for zone in zones_5m + zones_15m:
        if not isinstance(zone, dict): continue
        top    = zone.get("top") or 0
        bottom = zone.get("bottom") or 0
        if top == 0 and bottom == 0: continue
        center = zone.get("center", (top + bottom) / 2)
        if not ((bottom - threshold) <= sweep_level <= (top + threshold)): continue
        if direction == "bullish" and sweep_level <= center:
            zone_ok = True; break
        if direction == "bearish" and sweep_level >= center:
            zone_ok = True; break

    zone_score = 10 if zone_ok else 0

    # ── Step 7: Session timing ────────────────────────────────────────────
    sessions       = state.get("sessions", [])
    sessions_lower = [s.lower() for s in sessions]

    if any(s in sessions_lower for s in ["london", "ny", "new york"]):
        session_score = 10
    elif "asia" in sessions_lower or "asian" in sessions_lower:
        session_score = 0
    else:
        session_score = 0

    # ── Total score ───────────────────────────────────────────────────────
    total_score = sweep_score + reversal_score + market_score + precision_score + zone_score + session_score

    if debug:
        print(f"    [S2] {direction} | sweep={sweep_score} rev={reversal_score} mkt={market_score} "
              f"prec={precision_score} zone={zone_score} sess={session_score} → {total_score}")

    if total_score < config.MIN_CONFIDENCE:
        if debug: print(f"    [S2] skip: score {total_score} < {config.MIN_CONFIDENCE}")
        return None

    # ── SL / TP ───────────────────────────────────────────────────────────
    buf = config.SWEEP_SL_BUFFER_PIPS * pip

    if direction == "bullish":
        sl = sweep_level - buf
        if sl >= price:
            if debug: print("    [S2] skip: SL not below entry for BUY")
            return None
    else:
        sl = sweep_level + buf
        if sl <= price:
            if debug: print("    [S2] skip: SL not above entry for SELL")
            return None

    sl_dist = abs(price - sl)
    tp      = (price + sl_dist * config.TARGET_RR) if direction == "bullish" else (price - sl_dist * config.TARGET_RR)
    rr      = round(config.TARGET_RR, 2)
    sl      = round(sl, 5)
    tp      = round(tp, 5)

    spread_pips   = config.get_spread_pips(state.get("symbol"))
    spread_amount = spread_pips * pip
    net_tp_dist   = max(abs(tp - price) - spread_amount, 0.0)
    net_sl_dist   = sl_dist + spread_amount
    net_rr        = round(net_tp_dist / net_sl_dist, 2) if net_sl_dist > 0 else 0

    # ── Post filters ──────────────────────────────────────────────────────
    if dist_pips_sw > 25:
        print(f"    [S2] REJECTED: {dist_pips_sw:.1f}p from sweep (>25p hard limit)")
        return None

    candles_5m_raw = s5m.get("candles", [])
    body_threshold = 0.50 if conf_choch else 0.70
    reversal_ok    = False
    for c in reversed(candles_5m_raw[-6:]):
        o_ = c.get("open", 0); h_ = c.get("high", 0)
        l_ = c.get("low",  0); cl_= c.get("close", 0)
        if (cl_ > o_) if direction == "bullish" else (cl_ < o_):
            rng = h_ - l_; body = abs(cl_ - o_)
            if rng > 0 and (body / rng) >= body_threshold:
                reversal_ok = True; break

    if not reversal_ok:
        qual = "CHoCH-mild(50%)" if conf_choch else "BOS-displacement(70%)"
        print(f"    [S2] REJECTED: weak reversal candle ({qual})")
        return None

    if sl_dist < 7 * pip:
        print(f"    [S2] REJECTED: SL too tight ({sl_dist/pip:.1f}p < 7)")
        return None

    if abs(tp - price) / sl_dist < 1.5:
        print(f"    [S2] REJECTED: raw RR < 1.5")
        return None

    if net_rr < config.NET_MIN_RR:
        print(f"    [S2] REJECTED: net RR {net_rr} < {config.NET_MIN_RR}")
        return None

    sweep_type    = "CHoCH" if sweep_score == 25 else "BOS"
    reversal_type = "CHoCH" if reversal_score == 25 else "BOS"
    mkt_desc      = "range" if market_score == 15 else "slight-trend"
    reason        = (
        f"15M sweep={direction}({sweep_type}) @ {sweep_level:.5f} | "
        f"5M confirm={reversal_type} | mkt={mkt_desc} | "
        f"dist={dist_pips_sw:.1f}p prec={precision_score}pts | "
        f"zone={'✓' if zone_ok else '✗'} sess={sessions} | "
        f"spread={spread_pips}pip netRR={net_rr} score={total_score}/100"
    )

    return {
        "trade":       True,
        "type":        trade_type,
        "confidence":  total_score,
        "strategy":    "Liquidity Sweep Reversal Scalping",
        "reason":      reason,
        "entry":       price,
        "sl":          sl,
        "tp":          tp,
        "rr":          rr,
        "net_rr":      net_rr,
        "spread_pips": spread_pips,
    }