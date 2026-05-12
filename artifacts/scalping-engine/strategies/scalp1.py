"""
Strategy 1 — MTF Pullback Precision Scalping
=============================================
Trade in direction of higher timeframe bias (4H + 1H),
wait for pullback on 15M (HL / LH formation),
then use 5M BOS for precise entry with tight stop loss,
and ride the 15M impulse move.

Timeframe logic:
  Bias:             4H, 1H
  Pullback + Struct: 15M
  Entry + SL:        5M

All inputs come directly from STRUCT.ai — nothing is recomputed here.

Scoring breakdown (max 100):
  Bias alignment      — up to 30  (4H+1H+15M all agree=30, 4H+1H only=22, one TF=reject)
  15M pullback quality— up to 20  (clean HL/LH=20, weak=10, none=0)
  5M BOS strength     — up to 20  (strong=20, weak=10, none=reject)
  Entry location      — up to 15  (very close to 15M HL/LH=15, moderate=7, far=0/reject)
  Session timing      — up to 10  (London/NY=10, Asian=5, dead=0)
  Zone confluence     — up to 5   (near demand/supply zone=5)
"""

import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


def _last_price(structure: list, label: str) -> float | None:
    """Return the price of the most recent structure point with a given label."""
    for s in reversed(structure[-20:]):
        if not isinstance(s, dict):
            continue
        if s.get("label") == label:
            return s.get("price")
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

    # Require BOTH 4H and 1H to agree — single-TF alignment no longer accepted
    if both_bull:
        direction  = "bullish"
        trade_type = "BUY"
        # 15M also bullish = full confirmation (30pts)
        # 15M neutral      = partial (22pts — harder to reach 80, filters out weak setups)
        # 15M bearish      = counter-momentum, reject
        if bear_15m:
            if debug:
                print("    [MTF_PULLBACK] skip: 15M bearish counters bullish 4H+1H")
            return None
        bias_score = 30 if bull_15m else 22
    elif both_bear:
        direction  = "bearish"
        trade_type = "SELL"
        if bull_15m:
            if debug:
                print("    [MTF_PULLBACK] skip: 15M bullish counters bearish 4H+1H")
            return None
        bias_score = 30 if bear_15m else 22
    else:
        if debug:
            print("    [MTF_PULLBACK] skip: 4H and 1H not both aligned")
        return None

    # ── Step 2: 15M CHOCH invalidation (check before structure) ──────────
    choch_15m    = s15m.get("choch", [])
    recent_choch = choch_15m[-3:]

    if direction == "bullish" and any(c.get("direction") == "bearish" for c in recent_choch):
        if debug:
            print("    [MTF_PULLBACK] skip: bearish CHOCH on 15M invalidates bullish setup")
        return None

    if direction == "bearish" and any(c.get("direction") == "bullish" for c in recent_choch):
        if debug:
            print("    [MTF_PULLBACK] skip: bullish CHOCH on 15M invalidates bearish setup")
        return None

    # ── Step 3: 15M pullback quality — find the HL (BUY) or LH (SELL) ───
    struct_15m = s15m.get("structure", [])
    recent_15m = struct_15m[-12:]

    if direction == "bullish":
        pullback_label  = "HL"
        continue_label  = "HH"
    else:
        pullback_label  = "LH"
        continue_label  = "LL"

    pullback_price_15m = _last_price(struct_15m, pullback_label)

    if pullback_price_15m is None:
        if debug:
            print(f"    [MTF_PULLBACK] skip: no valid 15M {pullback_label} found")
        return None

    # Determine pullback quality: is the most recent structure point the pullback label?
    last_15m_item     = recent_15m[-1] if recent_15m else None
    most_recent_label = last_15m_item.get("label") if isinstance(last_15m_item, dict) else None
    has_continuation  = any(isinstance(s, dict) and s.get("label") == continue_label
                            for s in recent_15m)

    if most_recent_label == pullback_label and has_continuation:
        pullback_score = 20   # clean: e.g. HH confirmed → fresh HL just formed
    elif most_recent_label == pullback_label or has_continuation:
        pullback_score = 10   # partial: pullback or continuation present, not both
    else:
        pullback_score = 0    # no clear pullback structure

    # Guard: no pullback structure at all → reject immediately.
    # A score of 0 here means the 15M hasn't formed any HL/LH recently.
    # Without a pullback, this is a trend-chasing entry, not a precision scalp.
    if pullback_score == 0:
        if debug:
            print(f"    [MTF_PULLBACK] skip: no current 15M pullback structure (score=0)")
        return None

    # ── Step 4: Entry location — price must be near the 15M pullback level
    pip       = config.get_symbol_cfg(state.get("symbol"))["pip_size"]
    near_pips = config.NEAR_LEVEL_PIPS

    very_close_dist = near_pips * pip * 1.5    # 15 pips
    moderate_dist   = near_pips * pip * 3.0    # 30 pips
    overextended    = near_pips * pip * 5.0    # 50 pips — hard reject

    dist_from_pullback = abs(price - pullback_price_15m)

    if dist_from_pullback > overextended:
        if debug:
            print(f"    [MTF_PULLBACK] skip: price overextended {dist_from_pullback:.3f} from 15M {pullback_label}")
        return None

    if dist_from_pullback <= very_close_dist:
        location_score = 15
    elif dist_from_pullback <= moderate_dist:
        location_score = 7
    else:
        location_score = 0

    # ── Step 5: 5M BOS confirmation ──────────────────────────────────────
    bos_5m       = s5m.get("bos", [])
    matching_bos = [b for b in bos_5m[-6:] if isinstance(b, dict) and b.get("direction") == direction]

    if not matching_bos:
        if debug:
            print(f"    [MTF_PULLBACK] skip: no {direction} BOS on 5M")
        return None

    bos_score = 20 if len(matching_bos) >= 2 else 10

    # ── Step 6: Session timing ────────────────────────────────────────────
    sessions       = state.get("sessions", [])
    sessions_lower = [s.lower() for s in sessions]

    if any(s in sessions_lower for s in ["london", "ny", "new york"]):
        session_score = 10
    elif "asia" in sessions_lower or "asian" in sessions_lower:
        session_score = 5
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
        if not isinstance(zone, dict):
            continue
        top    = zone.get("top") or 0
        bottom = zone.get("bottom") or 0
        if top == 0 and bottom == 0:
            continue
        center = zone.get("center", (top + bottom) / 2)
        near   = (bottom - threshold) <= price <= (top + threshold)
        if not near:
            continue
        if direction == "bullish" and price <= center:
            zone_ok = True
            break
        if direction == "bearish" and price >= center:
            zone_ok = True
            break

    zone_score = 5 if zone_ok else 0

    # ── Total confluence score ────────────────────────────────────────────
    total_score = (
        bias_score + pullback_score + bos_score +
        location_score + session_score + zone_score
    )

    if debug:
        print(
            f"    [MTF_PULLBACK] {direction} | "
            f"bias={bias_score} pullback={pullback_score} bos={bos_score} "
            f"location={location_score} sess={session_score} zone={zone_score} "
            f"→ total={total_score}"
        )

    if total_score < config.MIN_CONFIDENCE:
        if debug:
            print(f"    [MTF_PULLBACK] skip: score {total_score} < {config.MIN_CONFIDENCE} minimum")
        return None

    # ── SL / TP calculation ───────────────────────────────────────────────
    buf            = config.SL_BUFFER_PIPS * pip   # 5 pips buffer
    struct_5m      = s5m.get("structure", [])
    struct_15m_pts = s15m.get("structure", [])
    _z5  = s5m.get("zones")  or []
    _z15 = s15m.get("zones") or []
    zones_all = (list(_z5) if isinstance(_z5, list) else []) + (list(_z15) if isinstance(_z15, list) else [])
    sr_all    = state.get("sr_levels") or []
    if not isinstance(sr_all, list): sr_all = []
    align_thresh   = near_pips * pip * 2   # 20 pips — 5M & 15M levels considered "aligned"
    zone_thresh    = near_pips * pip       # 10 pips — close enough to a zone

    def _is_strong_5m_level(lvl: float, kind: str) -> bool:
        """
        Return True if the 5M swing level is structurally defended by at least one of:
          1. Aligned with a 15M swing of the same kind (within align_thresh)
          2. Sitting inside or very near a supply/demand zone
          3. Sitting near an SR level of the matching kind
        A weak 5M level that passes none of these tests risks being taken out
        before TP — fall back to the 15M level instead.
        """
        # 1. Aligned with 15M structure
        lvl_15m = _last_price(struct_15m_pts, "HL" if kind == "support" else "LH")
        if lvl_15m is not None and abs(lvl - lvl_15m) <= align_thresh:
            return True
        # 2. Near / inside a demand (support) or supply (resistance) zone
        for zone in zones_all:
            top    = zone.get("top",    0)
            bottom = zone.get("bottom", 0)
            if top == 0 and bottom == 0:
                continue
            if (bottom - zone_thresh) <= lvl <= (top + zone_thresh):
                return True
        # 3. Near an SR level of matching kind
        sr_kind = "support" if kind == "support" else "resistance"
        for lvl_sr in sr_all:
            if lvl_sr.get("kind") == sr_kind:
                if abs(lvl_sr.get("price", 0) - lvl) <= zone_thresh:
                    return True
        return False

    sl_source = "5M"   # track which TF the SL came from (for debug / reason string)

    if direction == "bullish":
        sl_5m_lvl  = _last_price(struct_5m,      "HL")
        sl_15m_lvl = _last_price(struct_15m_pts, "HL")

        if sl_5m_lvl is not None and _is_strong_5m_level(sl_5m_lvl, "support"):
            sl_anchor = sl_5m_lvl           # 5M level is structurally defended — use it
        elif sl_15m_lvl is not None:
            sl_anchor = sl_15m_lvl          # 5M level is weak — fall back to 15M
            sl_source = "15M"
        else:
            # No structural level found — reject trade.
            # An arbitrary flat SL has no relationship to market structure
            # and will be hit randomly. Without a structural anchor, skip.
            if debug:
                print("    [MTF_PULLBACK] skip: no structural SL anchor found (no 5M HL, no 15M HL)")
            return None

        sl = sl_anchor - buf
        if sl >= price:
            if debug:
                print("    [MTF_PULLBACK] skip: SL not below entry for BUY")
            return None

    else:  # bearish
        sl_5m_lvl  = _last_price(struct_5m,      "LH")
        sl_15m_lvl = _last_price(struct_15m_pts, "LH")

        if sl_5m_lvl is not None and _is_strong_5m_level(sl_5m_lvl, "resistance"):
            sl_anchor = sl_5m_lvl           # 5M level is structurally defended — use it
        elif sl_15m_lvl is not None:
            sl_anchor = sl_15m_lvl          # 5M level is weak — fall back to 15M
            sl_source = "15M"
        else:
            # No structural level found — reject trade.
            if debug:
                print("    [MTF_PULLBACK] skip: no structural SL anchor found (no 5M LH, no 15M LH)")
            return None

        sl = sl_anchor + buf
        if sl <= price:
            if debug:
                print("    [MTF_PULLBACK] skip: SL not above entry for SELL")
            return None

    sl_dist = abs(price - sl)
    tp      = (price + sl_dist * config.TARGET_RR) if direction == "bullish" else (price - sl_dist * config.TARGET_RR)
    rr      = round(sl_dist * config.TARGET_RR / sl_dist, 2) if sl_dist > 0 else 0
    sl      = round(sl, 3)
    tp      = round(tp, 3)

    # ── Spread cost calculation ───────────────────────────────────────────
    # When you BUY at ASK (= MID + spread): your effective entry is worse by spread.
    # Net TP = TP_dist - spread  (spread eats into profit)
    # Net SL = SL_dist + spread  (spread adds to your risk exposure)
    spread_pips   = config.get_spread_pips(state.get("symbol"))
    spread_amount = spread_pips * pip
    tp_dist       = abs(tp - price)
    net_tp_dist   = max(tp_dist - spread_amount, 0.0)
    net_sl_dist   = sl_dist + spread_amount
    net_rr        = round(net_tp_dist / net_sl_dist, 2) if net_sl_dist > 0 else 0

    # ── Post-computation validation filters ──────────────────────────────
    # Filter 1: BOS quality — require ≥2 BOS OR 1 strong displacement candle
    if len(matching_bos) < 2:
        candles_5m_raw = s5m.get("candles", [])
        is_displacement = False
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
                if rng > 0 and (body / rng) >= 0.70:
                    is_displacement = True
                    break
        if not is_displacement:
            print(f"    [MTF_PULLBACK] REJECTED: weak BOS — 1 BOS, no displacement candle")
            return None

    # Filter 2: Price must be within 15 pips of the 15M pullback level
    max_entry_dist = near_pips * pip * 1.5   # 15 pips
    if dist_from_pullback > max_entry_dist:
        print(f"    [MTF_PULLBACK] REJECTED: too far from level ({dist_from_pullback / pip:.1f} pips > 15)")
        return None

    # Filter 3: SL distance must be at least MIN_SL_PIPS (set in config.py)
    if sl_dist < config.MIN_SL_PIPS * pip:
        print(f"    [MTF_PULLBACK] REJECTED: SL too tight ({sl_dist / pip:.1f} pips < {config.MIN_SL_PIPS})")
        return None

    # Filter 4: Effective RR (raw, before spread) must be ≥ 1.5
    actual_rr = round(abs(tp - price) / sl_dist, 2) if sl_dist > 0 else 0
    if actual_rr < 1.5:
        print(f"    [MTF_PULLBACK] REJECTED: RR too low ({actual_rr} < 1.5)")
        return None

    # Filter 5: Net RR after spread must be ≥ NET_MIN_RR (1.5)
    # Protects against spread wiping out acceptable setups on tight SLs
    if net_rr < config.NET_MIN_RR:
        print(f"    [MTF_PULLBACK] REJECTED: net RR after spread {net_rr} < {config.NET_MIN_RR} "
              f"(spread={spread_pips}pip eats too much of {sl_dist/pip:.1f}pip SL)")
        return None

    # ── Build reason string ───────────────────────────────────────────────
    pb_quality = "clean" if pullback_score == 20 else ("weak" if pullback_score == 10 else "none")
    bos_qual   = "strong" if bos_score == 20 else "weak"
    reason     = (
        f"4H={b4h} 1H={b1h} | "
        f"15M {pullback_label}={pb_quality}({pullback_score}pts) dist={dist_from_pullback:.3f} | "
        f"5M BOS={direction}({bos_qual}) | "
        f"location={location_score}pts | "
        f"session={sessions} | "
        f"zone={'✓' if zone_ok else '✗'} | "
        f"SL_anchor={sl_source} | "
        f"spread={spread_pips}pip netRR={net_rr} | "
        f"score={total_score}/100"
    )

    return {
        "trade":        True,
        "type":         trade_type,
        "confidence":   total_score,
        "strategy":     "MTF Pullback Precision Scalping",
        "reason":       reason,
        "entry":        price,
        "sl":           sl,
        "tp":           tp,
        "rr":           rr,
        "net_rr":       net_rr,
        "spread_pips":  spread_pips,
    }
