"""
Strategy 2 — Liquidity Sweep Reversal Scalping
===============================================
Exploit liquidity grabs (fake breakouts) where price sweeps previous 15M
highs/lows, traps traders, then reverses. Enter on 5M BOS/CHOCH confirmation
after the sweep, capturing the reversal move.

Timeframe logic:
  Context / Range:      15M  (sweep detection)
  Entry confirmation:   5M   (reversal trigger)

This strategy is the COMPLEMENT of Strategy 1:
  Strategy 1 = trending market → follow the trend
  Strategy 2 = ranging/mixed market → fade the sweep

All inputs come directly from STRUCT.ai — nothing is recomputed here.

Scoring breakdown (max 100):
  Liquidity sweep quality  — up to 25  (clear CHOCH-sweep=25, BOS-only=10, none=reject)
  5M reversal confirmation — up to 25  (strong CHOCH=25, BOS-only=10, none=reject)
  Market condition         — up to 15  (range/mixed=15, slight trend=5, strong trend=reject)
  Entry precision          — up to 15  (near sweep zone=15, extended=5, late=0)
  Zone confluence          — up to 10  (sweep aligns with supply/demand=10)
  Session timing           — up to 10  (London/NY=10, Asian=5, dead=0)
"""

import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


def _most_recent(items: list, key: str, value: str) -> dict | None:
    """Return the most recent item in list where item[key] == value.
    Limited to last 5 events — older sweeps are stale and no longer actionable."""
    for item in reversed(items[-5:]):
        if not isinstance(item, dict):
            continue
        if item.get(key) == value:
            return item
    return None


def check(state: dict, debug: bool = False) -> dict | None:
    if not isinstance(state, dict):
        return None

    bias  = state.get("bias", {})
    price = state.get("current_price")
    s5m   = state.get("5m", {})
    s15m  = state.get("15m", {})

    if not price or not isinstance(price, (int, float)) or not math.isfinite(price):
        return None

    # ── Step 1: Market condition — Strategy 2 avoids strong trends ────────
    b4h = bias.get("4h", "neutral")
    b1h = bias.get("1h", "neutral")

    bull_4h = b4h == "bullish"
    bear_4h = b4h == "bearish"
    bull_1h = b1h == "bullish"
    bear_1h = b1h == "bearish"

    strongly_trending = (bull_4h and bull_1h) or (bear_4h and bear_1h)
    slightly_trending = (bull_4h or bear_4h or bull_1h or bear_1h) and not strongly_trending
    ranging           = (b4h == "neutral" and b1h == "neutral")

    if strongly_trending:
        if debug:
            print("    [LIQ_SWEEP] skip: market strongly trending — use Strategy 1")
        return None

    market_score = 15 if (ranging or (not strongly_trending and not slightly_trending)) else 5
    if slightly_trending:
        market_score = 5

    # ── Step 2: Liquidity sweep detection on 15M ─────────────────────────
    bos_15m   = s15m.get("bos", [])
    choch_15m = s15m.get("choch", [])

    # BUY setup: bearish sweep of a 15M low followed by price recovering above it
    # SELL setup: bullish sweep of a 15M high followed by price dropping below it

    # Check for recent bearish event on 15M (sweep of lows → BUY reversal)
    bearish_choch_15m = _most_recent(choch_15m, "direction", "bearish")
    bearish_bos_15m   = _most_recent(bos_15m,   "direction", "bearish")

    # Check for recent bullish event on 15M (sweep of highs → SELL reversal)
    bullish_choch_15m = _most_recent(choch_15m, "direction", "bullish")
    bullish_bos_15m   = _most_recent(bos_15m,   "direction", "bullish")

    # Determine direction from sweep
    # BUY: price swept below a recent 15M low (bearish BOS/CHOCH) and is now recovering
    # SELL: price swept above a recent 15M high (bullish BOS/CHOCH) and is now reversing

    buy_sweep_price  = None
    sell_sweep_price = None
    buy_sweep_score  = 0
    sell_sweep_score = 0

    if bearish_choch_15m:
        buy_sweep_price  = bearish_choch_15m.get("price")
        buy_sweep_score  = 25   # CHOCH = strong sweep signal
    elif bearish_bos_15m:
        buy_sweep_price  = bearish_bos_15m.get("price")
        buy_sweep_score  = 10   # BOS only = weaker sweep

    if bullish_choch_15m:
        sell_sweep_price = bullish_choch_15m.get("price")
        sell_sweep_score = 25
    elif bullish_bos_15m:
        sell_sweep_price = bullish_bos_15m.get("price")
        sell_sweep_score = 10

    # ── Step 3: Verify reversal — price must be on correct side of sweep ──
    # BUY: price should now be ABOVE the bearish sweep level (came back up)
    # SELL: price should now be BELOW the bullish sweep level (came back down)
    pip       = config.get_symbol_cfg(state.get("symbol"))["pip_size"]
    near_pips = config.NEAR_LEVEL_PIPS

    direction      = None
    trade_type     = None
    sweep_score    = 0
    sweep_level    = None

    min_recovery = config.MIN_SWEEP_RECOVERY_PIPS * pip

    if buy_sweep_price is not None and (price - buy_sweep_price) >= min_recovery:
        # Price must have recovered at least MIN_SWEEP_RECOVERY_PIPS beyond the sweep low.
        # A smaller recovery is likely still inside the wick — not a confirmed reversal.
        direction   = "bullish"
        trade_type  = "BUY"
        sweep_score = buy_sweep_score
        sweep_level = buy_sweep_price

    if sell_sweep_price is not None and (sell_sweep_price - price) >= min_recovery:
        # If both are valid, pick the one with higher sweep quality
        if direction is None or sell_sweep_score > sweep_score:
            direction   = "bearish"
            trade_type  = "SELL"
            sweep_score = sell_sweep_score
            sweep_level = sell_sweep_price

    if direction is None:
        if debug:
            print("    [LIQ_SWEEP] skip: no valid liquidity sweep detected")
        return None

    if sweep_score == 0:
        if debug:
            print("    [LIQ_SWEEP] skip: sweep score 0 — no BOS/CHOCH on 15M")
        return None

    # ── Step 4: 5M reversal confirmation (CHOCH or BOS) ──────────────────
    bos_5m   = s5m.get("bos", [])
    choch_5m = s5m.get("choch", [])

    conf_choch = _most_recent(choch_5m, "direction", direction)
    conf_bos   = _most_recent(bos_5m,   "direction", direction)

    if conf_choch:
        reversal_score = 25   # CHOCH is the strongest reversal signal
    elif conf_bos:
        reversal_score = 10   # BOS alone is weaker but still valid
    else:
        if debug:
            print(f"    [LIQ_SWEEP] skip: no {direction} CHOCH or BOS on 5M to confirm reversal")
        return None

    # ── Step 5: Entry precision — how close is price to sweep zone ────────
    dist_from_sweep = abs(price - sweep_level)
    very_close_dist = near_pips * pip * 1.5    # 15 pips
    moderate_dist   = near_pips * pip * 3.0    # 30 pips
    too_far_dist    = near_pips * pip * 5.0    # 50 pips — too late

    if dist_from_sweep > too_far_dist:
        if debug:
            print(f"    [LIQ_SWEEP] skip: entry too far from sweep zone ({dist_from_sweep:.3f})")
        return None

    if dist_from_sweep <= very_close_dist:
        precision_score = 15
    elif dist_from_sweep <= moderate_dist:
        precision_score = 5
    else:
        precision_score = 0

    # ── Step 6: Zone confluence — does the sweep align with a S/D zone ───
    zones_5m  = s5m.get("zones") or []
    zones_15m = s15m.get("zones") or []
    if not isinstance(zones_5m,  list): zones_5m  = []
    if not isinstance(zones_15m, list): zones_15m = []
    threshold = near_pips * pip

    zone_ok = False
    for zone in zones_5m + zones_15m:
        if not isinstance(zone, dict):
            continue
        top    = zone.get("top") or 0
        bottom = zone.get("bottom") or 0
        if top == 0 and bottom == 0:
            continue
        center = zone.get("center", (top + bottom) / 2)
        near   = (bottom - threshold) <= sweep_level <= (top + threshold)
        if not near:
            continue
        if direction == "bullish" and sweep_level <= center:
            zone_ok = True
            break
        if direction == "bearish" and sweep_level >= center:
            zone_ok = True
            break

    zone_score = 10 if zone_ok else 0

    # ── Step 7: Session timing ────────────────────────────────────────────
    sessions       = state.get("sessions", [])
    sessions_lower = [s.lower() for s in sessions]

    if any(s in sessions_lower for s in ["london", "ny", "new york"]):
        session_score = 10
    elif "asia" in sessions_lower or "asian" in sessions_lower:
        session_score = 5
    else:
        session_score = 0

    # ── Total confluence score ────────────────────────────────────────────
    total_score = (
        sweep_score + reversal_score + market_score +
        precision_score + zone_score + session_score
    )

    if debug:
        print(
            f"    [LIQ_SWEEP] {direction} | "
            f"sweep={sweep_score} reversal={reversal_score} market={market_score} "
            f"precision={precision_score} zone={zone_score} sess={session_score} "
            f"→ total={total_score}"
        )

    if total_score < config.MIN_CONFIDENCE:
        if debug:
            print(f"    [LIQ_SWEEP] skip: score {total_score} < {config.MIN_CONFIDENCE} minimum")
        return None

    # ── SL / TP calculation ───────────────────────────────────────────────
    # SL placed beyond the sweep level (the trap point).
    # Uses SWEEP_SL_BUFFER_PIPS (8) instead of the normal 5-pip buffer because
    # liquidity sweeps routinely have a second wick to the same level (double-bottom /
    # double-top). The extra buffer absorbs that re-test before the real reversal.
    buf = config.SWEEP_SL_BUFFER_PIPS * pip

    if direction == "bullish":
        sl = sweep_level - buf          # below the sweep low
        if sl >= price:
            if debug:
                print("    [LIQ_SWEEP] skip: SL not below entry for BUY")
            return None
    else:
        sl = sweep_level + buf          # above the sweep high
        if sl <= price:
            if debug:
                print("    [LIQ_SWEEP] skip: SL not above entry for SELL")
            return None

    sl_dist = abs(price - sl)
    tp      = (price + sl_dist * config.TARGET_RR) if direction == "bullish" else (price - sl_dist * config.TARGET_RR)
    rr      = round(config.TARGET_RR, 2)
    sl      = round(sl, 3)
    tp      = round(tp, 3)

    # ── Spread cost calculation ───────────────────────────────────────────
    spread_pips   = config.get_spread_pips(state.get("symbol"))
    spread_amount = spread_pips * pip
    tp_dist       = abs(tp - price)
    net_tp_dist   = max(tp_dist - spread_amount, 0.0)
    net_sl_dist   = sl_dist + spread_amount
    net_rr        = round(net_tp_dist / net_sl_dist, 2) if net_sl_dist > 0 else 0

    # ── Post-computation validation filters ──────────────────────────────
    # Filter 1: Price must be within 25 pips of the sweep level
    max_sweep_dist = near_pips * pip * 2.5   # 25 pips
    if dist_from_sweep > max_sweep_dist:
        print(f"    [LIQ_SWEEP] REJECTED: far from sweep ({dist_from_sweep / pip:.1f} pips > 25)")
        return None

    # Filter 2: Reversal candle quality
    # CHOCH confirmation → mild check (body ≥ 50% of range — filters indecision candles)
    # BOS-only confirmation → strict check (body ≥ 70% — same as Strategy 1 displacement)
    candles_5m_raw = s5m.get("candles", [])
    body_threshold = 0.50 if conf_choch else 0.70
    reversal_ok    = False
    if candles_5m_raw:
        for c in reversed(candles_5m_raw[-6:]):
            o_  = c.get("open",  0)
            h_  = c.get("high",  0)
            l_  = c.get("low",   0)
            cl_ = c.get("close", 0)
            closes_in_dir = (cl_ > o_) if direction == "bullish" else (cl_ < o_)
            if not closes_in_dir:
                continue
            rng  = h_ - l_
            body = abs(cl_ - o_)
            if rng > 0 and (body / rng) >= body_threshold:
                reversal_ok = True
                break
    if not reversal_ok:
        qual = "CHOCH-mild(50%)" if conf_choch else "BOS-displacement(70%)"
        print(f"    [LIQ_SWEEP] REJECTED: weak reversal — {qual} candle check failed")
        return None

    # Filter 3: SL distance must be at least 7 pips
    if sl_dist < 7 * pip:
        print(f"    [LIQ_SWEEP] REJECTED: SL too tight ({sl_dist / pip:.1f} pips < 7)")
        return None

    # Filter 4: Effective RR (raw, before spread) must be ≥ 1.5
    actual_rr = round(abs(tp - price) / sl_dist, 2) if sl_dist > 0 else 0
    if actual_rr < 1.5:
        print(f"    [LIQ_SWEEP] REJECTED: RR too low ({actual_rr} < 1.5)")
        return None

    # Filter 5: Net RR after spread must be ≥ NET_MIN_RR (1.5)
    if net_rr < config.NET_MIN_RR:
        print(f"    [LIQ_SWEEP] REJECTED: net RR after spread {net_rr} < {config.NET_MIN_RR} "
              f"(spread={spread_pips}pip eats too much of {sl_dist/pip:.1f}pip SL)")
        return None

    # ── Build reason string ───────────────────────────────────────────────
    sweep_type    = "CHOCH" if sweep_score == 25 else "BOS"
    reversal_type = "CHOCH" if reversal_score == 25 else "BOS"
    mkt_desc      = "range" if market_score == 15 else "slight-trend"
    reason        = (
        f"15M sweep={direction}({sweep_type},{sweep_score}pts) @ {sweep_level:.3f} | "
        f"5M confirm={reversal_type}({reversal_score}pts) | "
        f"market={mkt_desc}({market_score}pts) | "
        f"prec={precision_score}pts dist={dist_from_sweep:.3f} | "
        f"zone={'✓' if zone_ok else '✗'} | "
        f"session={sessions} | "
        f"spread={spread_pips}pip netRR={net_rr} | "
        f"score={total_score}/100"
    )

    return {
        "trade":        True,
        "type":         trade_type,
        "confidence":   total_score,
        "strategy":     "Liquidity Sweep Reversal Scalping",
        "reason":       reason,
        "entry":        price,
        "sl":           sl,
        "tp":           tp,
        "rr":           rr,
        "net_rr":       net_rr,
        "spread_pips":  spread_pips,
    }
