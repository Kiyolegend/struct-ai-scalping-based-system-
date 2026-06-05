"""
Strategy 1 — MTF Pullback Precision Scalping
=============================================
Trade in direction of higher timeframe bias (4H + 1H),
wait for pullback on 15M (HL / LH formation),
then use 5M BOS for precise entry with tight stop loss,
and ride the 15M impulse move.

Session 2 fixes applied:
  - 24h age cap on 15M pullback label
  - 1h freshness check on 5M BOS events
  - Tighter distance scoring: 15/10/5 at ≤5/≤10/≤15 pips
  - Counter CHoCH window: 4h look-back with time check

Session 3 refinements applied:
  - Minimum score raised from 80 → 85 (forces stronger confluence)
  - 5M BOS freshness tightened from 2h → 1h (no stale triggers)
  - Distance hard reject tightened from >15p → >10p (no chase entries)
  - Distance scoring: ≤15p tier (5pts) removed — 10p is now the outer limit
  - Counter-BOS filter added: any counter-direction 5M BOS within 30min = skip

Quality upgrades applied (v2):
  - GATE: Post-BOS structure must hold — after the most recent 5M BOS fires,
    subsequent 5M candles must not make a new low (bullish) or new high (bearish)
    beyond the close of the very first post-BOS candle.
    Catches setups where the BOS was valid when it fired but the continuation has
    already failed — prevents entering into a broken 5M structure.
"""

import sys, os, math, time as _time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


def _last_label(structure: list, label: str | tuple) -> dict | None:
    """Return the most recent structure point dict with a given label."""
    labels = (label,) if isinstance(label, str) else label
    for s in reversed(structure[-20:]):
        if not isinstance(s, dict):
            continue
        if s.get("label") in labels:
            return s
    return None


# ── NEW: Post-BOS structure holds ─────────────────────────────────────────────
def _structure_holds(candles_5m: list, confirm_time: int, direction: str) -> bool:
    """
    After the 5M BOS timestamp, subsequent 5M candles must not violate the
    continuation by making a new low (bullish) or new high (bearish) beyond
    the close of the very first post-BOS candle.

    This catches the case where the BOS was genuine at the time it fired but
    price has since reversed — the pullback continuation has already failed
    and the setup should be skipped entirely.

    Returns True (structure intact) when:
      - Fewer than 2 candles exist after the event (too early to judge).
      - No subsequent candle has violated the anchor close.
    Returns False (structure broken) when a subsequent candle's low (bullish)
    or high (bearish) breaches the first post-BOS candle's close.
    """
    post = [c for c in candles_5m if c.get("time", 0) > confirm_time]
    if len(post) < 2:
        return True

    anchor_close = post[0].get("close", 0)
    subsequent   = post[1:]

    if direction == "bullish":
        return not any(c.get("low", 0) < anchor_close for c in subsequent)
    else:
        return not any(c.get("high", float("inf")) > anchor_close for c in subsequent)


def check(state: dict, debug: bool = False) -> dict | None:
    if not isinstance(state, dict):
        return None

    bias  = state.get("bias", {})
    price = state.get("current_price")
    s5m   = state.get("5m", {})
    s15m  = state.get("15m", {})
    candles_5m = s5m.get("candles", [])

    if not price or not isinstance(price, (int, float)) or not math.isfinite(price):
        return None

    # ── Step 1: Higher timeframe bias alignment ──────────────────────────
    b4h  = bias.get("4h",  "neutral")
    b1h  = bias.get("1h",  "neutral")
    b15m = bias.get("15m", "neutral")

    bull_4h  = b4h  == "bullish"
    bear_4h  = b4h  == "bearish"
    bull_1h  = b1h  == "bullish"
    bear_1h  = b1h  == "bearish"
    bull_15m = b15m == "bullish"
    bear_15m = b15m == "bearish"

    both_bull = bull_4h and bull_1h
    both_bear = bear_4h and bear_1h

    if both_bull:
        direction  = "bullish"
        trade_type = "BUY"
        if bear_15m:
            if debug: print("    [S1] skip: 15M bearish counters bullish 4H+1H")
            return None
        bias_score = 30 if bull_15m else 22
    elif both_bear:
        direction  = "bearish"
        trade_type = "SELL"
        if bull_15m:
            if debug: print("    [S1] skip: 15M bullish counters bearish 4H+1H")
            return None
        bias_score = 30 if bear_15m else 22
    else:
        if debug: print("    [S1] skip: 4H and 1H not both aligned")
        return None

    # ── Step 2: 15M CHoCH invalidation — 4h look-back with time check ───
    now_sec = int(state.get("reference_ts") or _time.time())
    choch_15m    = s15m.get("choch", [])
    recent_choch = [c for c in choch_15m
                    if isinstance(c, dict) and (now_sec - c.get("time", 0)) <= 4 * 3600][-3:]

    if direction == "bullish" and any(c.get("direction") == "bearish" for c in recent_choch):
        if debug: print("    [S1] skip: bearish CHoCH on 15M (within 4h)")
        return None
    if direction == "bearish" and any(c.get("direction") == "bullish" for c in recent_choch):
        if debug: print("    [S1] skip: bullish CHoCH on 15M (within 4h)")
        return None

    # ── Step 3: 15M pullback — 24h age cap ───────────────────────────────
    struct_15m = s15m.get("structure", [])
    recent_15m = struct_15m[-12:]

    pullback_label = "HL" if direction == "bullish" else "LH"
    continue_label = "HH" if direction == "bullish" else "LL"

    pullback_item = _last_label(struct_15m, pullback_label)

    if pullback_item is None:
        if debug: print(f"    [S1] skip: no 15M {pullback_label} found")
        return None

    pb_time = pullback_item.get("time", 0)
    if pb_time and (now_sec - pb_time) > 24 * 3600:
        if debug: print(f"    [S1] skip: 15M {pullback_label} is stale ({(now_sec - pb_time)//3600}h ago)")
        return None

    pullback_price_15m = pullback_item.get("price")
    if pullback_price_15m is None:
        return None

    last_15m_item     = recent_15m[-1] if recent_15m else None
    most_recent_label = last_15m_item.get("label") if isinstance(last_15m_item, dict) else None
    has_continuation  = any(isinstance(s, dict) and s.get("label") == continue_label for s in recent_15m)

    if most_recent_label == pullback_label and has_continuation:
        pullback_score = 20
    elif most_recent_label == pullback_label or has_continuation:
        pullback_score = 10
    else:
        pullback_score = 0

    if pullback_score == 0:
        if debug: print(f"    [S1] skip: no current 15M pullback structure")
        return None

    # ── Step 4: Entry location — tighter distance scoring ────────────────
    pip                = config.get_symbol_cfg(state.get("symbol"))["pip_size"]
    near_pips          = config.NEAR_LEVEL_PIPS
    dist_from_pullback = abs(price - pullback_price_15m)
    dist_pips          = dist_from_pullback / pip

    if dist_pips > 10:
        if debug: print(f"    [S1] skip: price {dist_pips:.1f}p from pullback (>10p)")
        return None

    if dist_pips <= 5:
        location_score = 15
    elif dist_pips <= 10:
        location_score = 10
    else:
        location_score = 0

    # ── Step 5: 5M BOS — with 1h freshness check ─────────────────────────
    bos_5m       = s5m.get("bos", [])
    matching_bos = [
        b for b in bos_5m[-6:]
        if isinstance(b, dict)
        and b.get("direction") == direction
        and (now_sec - b.get("time", now_sec)) <= 1 * 3600
    ]

    if not matching_bos:
        if debug: print(f"    [S1] skip: no {direction} BOS on 5M (or all stale >1h)")
        return None

    bos_score = 20 if len(matching_bos) >= 2 else 10

    # ── Step 5b: Counter-BOS rejection ───────────────────────────────────
    # If a counter-direction BOS fired on 5M in the last 30 minutes, the
    # structure is two-sided — directional conviction is weak. Skip.
    counter_dir = "bearish" if direction == "bullish" else "bullish"
    counter_bos = [
        b for b in bos_5m[-6:]
        if isinstance(b, dict)
        and b.get("direction") == counter_dir
        and (now_sec - b.get("time", now_sec)) <= 30 * 60
    ]
    if counter_bos:
        if debug: print(f"    [S1] skip: counter {counter_dir} BOS on 5M within 30min")
        return None

    # ── NEW GATE: Post-BOS structure must hold ────────────────────────────
    # After the most recent valid BOS fired, the pullback continuation must
    # still be intact. If subsequent 5M candles have already broken back
    # through the first post-BOS close, the setup has failed — skip and wait
    # for a fresh BOS rather than entering a broken continuation.
    recent_bos_sorted = sorted(matching_bos, key=lambda x: x.get("time", 0), reverse=True)
    confirm_time_bos  = recent_bos_sorted[0].get("time", 0) if recent_bos_sorted else 0
    if confirm_time_bos > 0 and candles_5m:
        if not _structure_holds(candles_5m, confirm_time_bos, direction):
            if debug:
                print(f"    [S1] skip: post-BOS structure violated "
                      f"— {direction} continuation already invalidated, waiting for fresh BOS")
            return None

    # ── Step 6: Session timing ────────────────────────────────────────────
    sessions       = state.get("sessions", [])
    sessions_lower = [s.lower() for s in sessions]

    if any(s in sessions_lower for s in ["london", "ny", "new york"]):
        session_score = 10
    elif "asia" in sessions_lower or "asian" in sessions_lower:
        session_score = 0
    else:
        session_score = 0

    # ── Step 7: Zone confluence (5pts) ───────────────────────────────────
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
        near   = (bottom - threshold) <= price <= (top + threshold)
        if not near: continue
        if direction == "bullish" and price <= center:
            zone_ok = True; break
        if direction == "bearish" and price >= center:
            zone_ok = True; break

    zone_score = 5 if zone_ok else 0

    # ── Total score ───────────────────────────────────────────────────────
    total_score = bias_score + pullback_score + bos_score + location_score + session_score + zone_score

    # ── Displacement upgrade (must run BEFORE threshold gate) ─────────────
    # If only one BOS, check for a strong displacement candle (body ≥ 70%).
    # A single BOS with clear displacement is upgraded to equal a double BOS.
    # This must happen before the MIN_CONFIDENCE check so the upgraded score
    # is what gets evaluated — not the lower pre-upgrade score.
    if len(matching_bos) < 2:
        is_displacement = False
        for c in reversed(candles_5m[-6:]):
            o_ = c.get("open", 0); h_ = c.get("high", 0)
            l_ = c.get("low",  0); cl_= c.get("close",0)
            if (cl_ > o_) if direction == "bullish" else (cl_ < o_):
                rng = h_ - l_; body = abs(cl_ - o_)
                if rng > 0 and (body / rng) >= 0.70:
                    is_displacement = True; break
        if not is_displacement:
            print("    [S1] REJECTED: weak BOS — 1 BOS, no displacement candle")
            return None
        bos_score = 20  # single strong BOS upgraded to match TradeTeller scoring
        total_score = bias_score + pullback_score + bos_score + location_score + session_score + zone_score

    if debug:
        print(f"    [S1] {direction} | bias={bias_score} pb={pullback_score} bos={bos_score} "
              f"loc={location_score} sess={session_score} zone={zone_score} → {total_score}")

    if total_score < max(85, config.MIN_CONFIDENCE):
        if debug: print(f"    [S1] skip: score {total_score} < {max(85, config.MIN_CONFIDENCE)}")
        return None

    # ── SL / TP ───────────────────────────────────────────────────────────
    buf            = config.SL_BUFFER_PIPS * pip
    struct_5m      = s5m.get("structure", [])
    struct_15m_pts = s15m.get("structure", [])
    _z5  = s5m.get("zones")  or []
    _z15 = s15m.get("zones") or []
    zones_all      = (list(_z5) if isinstance(_z5, list) else []) + (list(_z15) if isinstance(_z15, list) else [])
    sr_all         = state.get("sr_levels") or []
    if not isinstance(sr_all, list): sr_all = []
    align_thresh   = near_pips * pip * 2
    zone_thresh    = near_pips * pip

    def _is_strong_5m_level(lvl: float, kind: str) -> bool:
        lvl_15m = (_last_label(struct_15m_pts, ("HL", "EQL") if kind == "support" else ("LH", "EQH")) or {}).get("price")
        if lvl_15m is not None and abs(lvl - lvl_15m) <= align_thresh:
            return True
        for zone in zones_all:
            t = zone.get("top", 0); b = zone.get("bottom", 0)
            if t == 0 and b == 0: continue
            if (b - zone_thresh) <= lvl <= (t + zone_thresh): return True
        sr_kind = "support" if kind == "support" else "resistance"
        for lvl_sr in sr_all:
            if lvl_sr.get("kind") == sr_kind and abs(lvl_sr.get("price", 0) - lvl) <= zone_thresh:
                return True
        return False

    sl_source = "5M"

    if direction == "bullish":
        sl_5m_lvl  = (_last_label(struct_5m,      ("HL", "EQL")) or {}).get("price")
        sl_15m_lvl = (_last_label(struct_15m_pts, ("HL", "EQL")) or {}).get("price")
        if sl_5m_lvl is not None and _is_strong_5m_level(sl_5m_lvl, "support"):
            sl_anchor = sl_5m_lvl
        elif sl_15m_lvl is not None:
            sl_anchor = sl_15m_lvl; sl_source = "15M"
        else:
            if debug: print("    [S1] skip: no structural SL anchor")
            return None
        sl = sl_anchor - buf
        if sl >= price:
            if debug: print("    [S1] skip: SL not below entry for BUY")
            return None
    else:
        sl_5m_lvl  = (_last_label(struct_5m,      ("LH", "EQH")) or {}).get("price")
        sl_15m_lvl = (_last_label(struct_15m_pts, ("LH", "EQH")) or {}).get("price")
        if sl_5m_lvl is not None and _is_strong_5m_level(sl_5m_lvl, "resistance"):
            sl_anchor = sl_5m_lvl
        elif sl_15m_lvl is not None:
            sl_anchor = sl_15m_lvl; sl_source = "15M"
        else:
            if debug: print("    [S1] skip: no structural SL anchor")
            return None
        sl = sl_anchor + buf
        if sl <= price:
            if debug: print("    [S1] skip: SL not above entry for SELL")
            return None

    sl_dist = abs(price - sl)
    _fib = config.fib_extension_tp(state, direction, price)
    tp   = _fib if _fib is not None else (
               (price + sl_dist *config.TARGET_RR) if direction == "bullish" else (price - sl_dist * config.TARGET_RR))
    rr      = round(config.TARGET_RR, 2)
    sl      = round(sl, 5)
    tp      = round(tp, 5)

    total_cost_pips = config.get_total_cost_pips(state.get("symbol"))
    spread_pips     = config.get_spread_pips(state.get("symbol"))   # kept for logging
    cost_amount     = total_cost_pips * pip
    net_tp_dist     = max(abs(tp - price) - cost_amount, 0.0)
    net_sl_dist     = sl_dist + cost_amount
    net_rr          = round(net_tp_dist / net_sl_dist, 2) if net_sl_dist > 0 else 0
    # ── Post filters ──────────────────────────────────────────────────────
    

    if sl_dist < config.MIN_SL_PIPS * pip:
        print(f"    [S1] REJECTED: SL too tight ({sl_dist/pip:.1f}p < {config.MIN_SL_PIPS})")
        return None

    if abs(tp - price) / sl_dist < 1.5:
        print(f"    [S1] REJECTED: raw RR < 1.5")
        return None

    if net_rr < config.NET_MIN_RR:
        print(f"    [S1] REJECTED: net RR {net_rr} < {config.NET_MIN_RR}")
        return None

    pb_age_h = round((now_sec - pb_time) / 3600, 1) if pb_time else "?"
    pb_qual  = "clean" if pullback_score == 20 else "weak"
    reason   = (
        f"4H={b4h} 1H={b1h} | "
        f"15M {pullback_label}={pb_qual} age={pb_age_h}h dist={dist_pips:.1f}p | "
        f"5M BOS {len(matching_bos)}× ✓held | loc={location_score}pts | "
        f"sess={sessions} zone={'✓' if zone_ok else '✗'} SL={sl_source} | "
        f"spread={spread_pips}pip netRR={net_rr} score={total_score}/100"
    )

    return {
        "trade":       True,
        "type":        trade_type,
        "confidence":  total_score,
        "strategy":    "MTF Pullback Precision Scalping",
        "reason":      reason,
        "entry":       price,
        "sl":          sl,
        "tp":          tp,
        "rr":          rr,
        "net_rr":      net_rr,
        "spread_pips": spread_pips,
        "total_cost_pips":  total_cost_pips,
    }