"""
Strategy 3 — ICT Order Block / FVG Zone Reaction
=================================================
Trades institutional Order Block levels with Fair Value Gap confluence.
The highest-quality setup in the engine — fires infrequently but precisely.

Timeframe logic:
  4H + 1H  — bias direction
  1H       — Order Block detection (unmitigated OBs)
  4H       — OB stacking bonus (confluence)
  5M       — FVG detection + entry confirmation (CHoCH/BOS)

Scoring breakdown (max ~115):
  HTF alignment        — up to 15  (4H+1H agree=15, 4H only=8, conflict=reject)
  1H OB found          — 25        (unmitigated OB in bias direction)
  4H OB stacking       — 15        (4H OB overlaps 1H OB — institutional confluence)
  5M FVG overlap       — 10        (FVG inside the OB zone)
  Price in/near OB     — up to 20  (inside OB=20, within 10p=10, far=0/wait)
  Session timing       — 10        (London/NY=10)
  5M confirmation      — up to 20  (CHoCH body≥50%=20, BOS body≥60%=10)

Minimum score: config.MIN_CONFIDENCE (default 80)
Entry = market, SL = beyond OB edge + 5p buffer, TP = 2R
"""

import sys, os, math, time as _time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


# ─────────────────────────────────────────────────────────────────────────────
# Order Block detection — ported from TradingChart.tsx detectOrderBlocks()
# ─────────────────────────────────────────────────────────────────────────────

def detect_order_blocks(candles: list, current_price: float, symbol: str = "") -> list:
    """
    Detect unmitigated Order Blocks in a candle list.
    Returns list of dicts: {type, top, bottom, time}
    type = 'bullish' (price expected to bounce up) or 'bearish' (bounce down)

    Bullish OB: a bearish candle (close < open) that is immediately followed
    by an impulsive move upward that breaks above the candle's high.
    Bearish OB: a bullish candle (close > open) followed by an impulsive
    break below the candle's low.

    Mitigation: if any future candle closes clearly beyond the OB boundary
    (2-pip buffer), the OB is consumed and not returned.
    """
    n = len(candles)
    if n < 10:
        return []

    pip      = config.get_symbol_cfg(symbol)["pip_size"]
    min_size = 5 * pip
    proximity = min(0.015, (60 * pip) / current_price)  # cap at 60 pips

    results = []

    for i in range(1, n - 3):
        c = candles[i]

        # Average range of the last 10 bars (impulse proxy)
        lookback = candles[max(0, i - 10):i]
        avg_range = (
            sum(x["high"] - x["low"] for x in lookback) / len(lookback)
            if lookback else 0
        )

        # ── Bullish OB: bearish candle followed by impulsive move up ─────
        if c["close"] < c["open"]:
            slice_fwd  = candles[i + 1: min(i + 6, n)]
            future_high = max((x["close"] for x in slice_fwd), default=0)

            if future_high > c["high"] and (c["high"] - c["low"]) >= min_size:
                break_candle = max(slice_fwd, key=lambda x: x["high"] - x["low"], default=None)
                has_displacement = (
                    avg_range > 0 and break_candle is not None and
                    (break_candle["high"] - break_candle["low"]) >= 1.5 * avg_range
                )
                if not has_displacement:
                    continue

                center = (c["high"] + c["low"]) / 2
                dist   = abs(center - current_price) / current_price

                if dist <= proximity:
                    mitigated = any(
                        float(fc["close"]) < float(c["low"]) - 2 * pip
                        for fc in candles[i + 1:]
                    )
                    if not mitigated:
                        results.append({
                            "type": "bullish",
                            "top":    round(c["high"], 5),
                            "bottom": round(c["low"],  5),
                            "dist":   dist,
                            "time":   c.get("time", 0),
                        })

        # ── Bearish OB: bullish candle followed by impulsive move down ───
        if c["close"] > c["open"]:
            slice_fwd = candles[i + 1: min(i + 6, n)]
            future_low = min((x["close"] for x in slice_fwd), default=float("inf"))

            if future_low < c["low"] and (c["high"] - c["low"]) >= min_size:
                break_candle = max(slice_fwd, key=lambda x: x["high"] - x["low"], default=None)
                has_displacement = (
                    avg_range > 0 and break_candle is not None and
                    (break_candle["high"] - break_candle["low"]) >= 1.5 * avg_range
                )
                if not has_displacement:
                    continue

                center = (c["high"] + c["low"]) / 2
                dist   = abs(center - current_price) / current_price

                if dist <= proximity:
                    mitigated = any(
                        float(fc["close"]) > float(c["high"]) + 2 * pip
                        for fc in candles[i + 1:]
                    )
                    if not mitigated:
                        results.append({
                            "type": "bearish",
                            "top":    round(c["high"], 5),
                            "bottom": round(c["low"],  5),
                            "dist":   dist,
                            "time":   c.get("time", 0),
                        })

    # Return closest bullish OB below price + closest bearish OB above price
    bull = sorted(
        [o for o in results if o["type"] == "bullish" and (o["top"] + o["bottom"]) / 2 <= current_price],
        key=lambda x: x["dist"]
    )[:1]
    bear = sorted(
        [o for o in results if o["type"] == "bearish" and (o["top"] + o["bottom"]) / 2 >= current_price],
        key=lambda x: x["dist"]
    )[:1]

    return [{"type": o["type"], "top": o["top"], "bottom": o["bottom"], "time": o["time"]}
            for o in bull + bear]


# ─────────────────────────────────────────────────────────────────────────────
# Fair Value Gap detection — ported from TradingChart.tsx detectFVGs()
# ─────────────────────────────────────────────────────────────────────────────

def detect_fvgs(candles: list, current_price: float, symbol: str = "") -> list:
    """
    Detect unmitigated Fair Value Gaps in a candle list.
    Returns list of dicts: {type, top, bottom}

    Bullish FVG: gap between prev candle's high and next candle's low (price moved up fast).
    Bearish FVG: gap between next candle's high and prev candle's low (price moved down fast).
    FVGs older than 48h or that have been revisited are considered mitigated.
    """
    n = len(candles)
    if n < 3:
        return []

    pip       = config.get_symbol_cfg(symbol)["pip_size"]
    min_gap   = 3 * pip
    proximity = min(0.01, (100 * pip) / current_price)

    results = []

    for i in range(1, n - 1):
        prev = candles[i - 1]
        next_ = candles[i + 1]

        # Bullish FVG: gap up (next low > prev high)
        b_top    = next_["low"]
        b_bottom = prev["high"]
        if b_top > b_bottom and (b_top - b_bottom) >= min_gap:
            center = (b_top + b_bottom) / 2
            dist   = abs(center - current_price) / current_price
            if dist <= proximity:
                mitigated = any(fc["low"] <= b_bottom for fc in candles[i + 2:])
                if not mitigated:
                    results.append({
                        "type": "bullish",
                        "top":    round(b_top,    5),
                        "bottom": round(b_bottom, 5),
                        "dist":   dist,
                    })

        # Bearish FVG: gap down (next high < prev low)
        d_top    = prev["low"]
        d_bottom = next_["high"]
        if d_top > d_bottom and (d_top - d_bottom) >= min_gap:
            center = (d_top + d_bottom) / 2
            dist   = abs(center - current_price) / current_price
            if dist <= proximity:
                mitigated = any(fc["high"] >= d_top for fc in candles[i + 2:])
                if not mitigated:
                    results.append({
                        "type": "bearish",
                        "top":    round(d_top,    5),
                        "bottom": round(d_bottom, 5),
                        "dist":   dist,
                    })

    bull = sorted(
        [f for f in results if f["type"] == "bullish" and (f["top"] + f["bottom"]) / 2 <= current_price],
        key=lambda x: x["dist"]
    )[:1]
    bear = sorted(
        [f for f in results if f["type"] == "bearish" and (f["top"] + f["bottom"]) / 2 >= current_price],
        key=lambda x: x["dist"]
    )[:1]

    return [{"type": f["type"], "top": f["top"], "bottom": f["bottom"]} for f in bull + bear]


# ─────────────────────────────────────────────────────────────────────────────
# S3 main check
# ─────────────────────────────────────────────────────────────────────────────

def check(state: dict, debug: bool = False) -> dict | None:
    if not isinstance(state, dict):
        return None

    bias  = state.get("bias", {})
    price = state.get("current_price")
    s5m   = state.get("5m", {})
    s1h   = state.get("1h", {})
    s4h   = state.get("4h", {})

    if not price or not isinstance(price, (int, float)) or not math.isfinite(price):
        return None

    candles_1h = s1h.get("candles", [])
    candles_5m = s5m.get("candles", [])
    candles_4h = s4h.get("candles", [])

    if not candles_1h:
        print("    [S3] WARN: no 1H candles from STRUCT.ai — skipping (API issue?)")
        return None
    # ── Step 1: HTF direction ─────────────────────────────────────────────
    b4h = bias.get("4h", "neutral")
    b1h = bias.get("1h", "neutral")

    if b4h not in ("bullish", "bearish"):
        if debug: print("    [S3] skip: no clear 4H bias")
        return None

    if b1h not in ("bullish", "bearish"):
        # 4H alone gives partial alignment
        direction   = "bullish" if b4h == "bullish" else "bearish"
        align_score = 8
    elif b4h != b1h:
        if debug: print(f"    [S3] skip: 4H {b4h} vs 1H {b1h} conflicting")
        return None
    else:
        direction   = "bullish" if b4h == "bullish" else "bearish"
        align_score = 15

    trade_type = "BUY" if direction == "bullish" else "SELL"
    symbol     = state.get("symbol", "")
    pip        = config.get_symbol_cfg(symbol)["pip_size"]
    now_sec    = int(_time.time())

    # ── Step 2: 1H unmitigated OB in bias direction (within 48h) ─────────
    obs_1h = [
        ob for ob in detect_order_blocks(candles_1h, price, symbol)
        if ob["type"] == ("bullish" if direction == "bullish" else "bearish")
        and (now_sec - ob.get("time", 0)) <= 48 * 3600
    ]

    if not obs_1h:
        if debug: print(f"    [S3] skip: no unmitigated 1H {'bullish' if direction=='bullish' else 'bearish'} OB near price")
        return None

    ob = obs_1h[0]
    ob_score = 25

    # ── Step 2b: 4H OB stacking bonus ────────────────────────────────────
    ob4h_score = 0
    ob4h_label = ""
    if candles_4h:
        obs_4h = [
            o for o in detect_order_blocks(candles_4h, price, symbol)
            if o["type"] == ("bullish" if direction == "bullish" else "bearish")
        ]
        if obs_4h:
            ob4h = obs_4h[0]
            # Check overlap: 4H OB bottom < 1H OB top AND 4H OB top > 1H OB bottom
            if ob4h["bottom"] < ob["top"] and ob4h["top"] > ob["bottom"]:
                ob4h_score = 15
                ob4h_label = "4H+1H stacked"

    # ── Step 3: 5M FVG overlapping the OB ────────────────────────────────
    fvg_score = 0
    fvg_label = ""
    if candles_5m:
        fvgs_5m = [
            f for f in detect_fvgs(candles_5m, price, symbol)
            if f["type"] == ("bullish" if direction == "bullish" else "bearish")
        ]
        if fvgs_5m:
            fvg = fvgs_5m[0]
            # Check overlap with OB
            if fvg["bottom"] < ob["top"] and fvg["top"] > ob["bottom"]:
                fvg_score = 10
                fvg_label = "+FVG"

    # ── Step 4: Price position relative to OB ────────────────────────────
    inside_ob   = ob["bottom"] <= price <= ob["top"]
    ob_center   = (ob["top"] + ob["bottom"]) / 2
    dist_center = abs(price - ob_center) / pip

    if inside_ob:
        zone_score = 20
    elif dist_center <= 10:
        zone_score = 10
    else:
        if debug:
            ob_edge = ob["top"] if direction == "bullish" else ob["bottom"]
            print(f"    [S3] waiting: OB @ {ob_edge:.5f}, price {dist_center:.1f}p from center")
        return None

    # ── Step 5: Session timing ────────────────────────────────────────────
    sessions       = state.get("sessions", [])
    sessions_lower = [s.lower() for s in sessions]

    if any(s in sessions_lower for s in ["london", "ny", "new york"]):
        session_score = 10
    elif "asia" in sessions_lower or "asian" in sessions_lower:
        session_score = 0
    else:
        session_score = 0

    # ── Step 6: 5M confirmation — CHoCH or BOS within 1h ─────────────────
    bos_5m   = s5m.get("bos", [])
    choch_5m = s5m.get("choch", [])
    conf_dir = direction  # "bullish" or "bearish"

    conf_choch = next(
        (c for c in sorted(choch_5m, key=lambda x: x.get("time", 0), reverse=True)
         if isinstance(c, dict) and c.get("direction") == conf_dir
         and (now_sec - c.get("time", 0)) <= 3600), None
    )
    conf_bos = next(
        (b for b in sorted(bos_5m, key=lambda x: x.get("time", 0), reverse=True)
         if isinstance(b, dict) and b.get("direction") == conf_dir
         and (now_sec - b.get("time", 0)) <= 3600), None
    )

    confirm_score = 0
    confirm_type  = ""

    if conf_choch:
        # Check body strength ≥ 50%
        candle = next((c for c in candles_5m if c.get("time") == conf_choch.get("time")), None)
        body_ok = True
        if candle:
            rng  = candle["high"] - candle["low"]
            body = abs(candle["close"] - candle["open"])
            body_ok = rng > 0 and (body / rng) >= 0.50
        if body_ok:
            confirm_score = 20
            confirm_type  = "CHoCH"

    if confirm_score == 0 and conf_bos:
        # Check body strength ≥ 60%
        candle = next((c for c in candles_5m if c.get("time") == conf_bos.get("time")), None)
        body_ok = True
        if candle:
            rng  = candle["high"] - candle["low"]
            body = abs(candle["close"] - candle["open"])
            body_ok = rng > 0 and (body / rng) >= 0.60
        if body_ok:
            confirm_score = 10
            confirm_type  = "BOS"

    if confirm_score == 0:
        if debug:
            in_str = "Inside OB" if inside_ob else "Near OB"
            print(f"    [S3] waiting: {in_str}{fvg_label} — need 5M {direction} confirmation")
        return None

    # ── Total score ───────────────────────────────────────────────────────
    total_score = (
        align_score + ob_score + ob4h_score + fvg_score +
        zone_score + session_score + confirm_score
    )

    if debug:
        print(f"    [S3] {direction} | align={align_score} ob={ob_score} ob4h={ob4h_score} "
              f"fvg={fvg_score} zone={zone_score} sess={session_score} conf={confirm_score} → {total_score}")

    if total_score < config.MIN_CONFIDENCE:
        if debug: print(f"    [S3] skip: score {total_score}  < {config.MIN_CONFIDENCE} minimum")
        return None

    # ── SL / TP — SL beyond OB edge ──────────────────────────────────────
    buf = config.SL_BUFFER_PIPS * pip

    if direction == "bullish":
        sl = ob["bottom"] - buf
        if sl >= price:
            if debug: print("    [S3] skip: SL not below entry for BUY")
            return None
    else:
        sl = ob["top"] + buf
        if sl <= price:
            if debug: print("    [S3] skip: SL not above entry for SELL")
            return None

    sl_dist = abs(price - sl)

    if sl_dist < config.MIN_SL_PIPS * pip:
        print(f"    [S3] REJECTED: SL too tight ({sl_dist/pip:.1f}p < {config.MIN_SL_PIPS})")
        return None

    tp    = (price + sl_dist * config.TARGET_RR) if direction == "bullish" else (price - sl_dist * config.TARGET_RR)
    rr    = round(config.TARGET_RR, 2)
    sl    = round(sl, 5)
    tp    = round(tp, 5)

    total_cost_pips = config.get_total_cost_pips(state.get("symbol"))
    spread_pips     = config.get_spread_pips(state.get("symbol"))   # kept for logging
    cost_amount     = total_cost_pips * pip
    net_tp_dist     = max(abs(tp - price) - cost_amount, 0.0)
    net_sl_dist     = sl_dist + cost_amount
    net_rr          = round(net_tp_dist / net_sl_dist, 2) if net_sl_dist > 0 else 0

    if abs(tp - price) / sl_dist < 1.5:
        print("    [S3] REJECTED: raw RR < 1.5")
        return None

    if net_rr < config.NET_MIN_RR:
        print(f"    [S3] REJECTED: net RR {net_rr} < {config.NET_MIN_RR}")
        return None

    ob_desc = ob4h_label if ob4h_label else "1H OB"
    in_str  = "inside" if inside_ob else "near"
    reason  = (
        f"{in_str} {ob_desc} [{ob['bottom']:.5f}-{ob['top']:.5f}]{fvg_label} | "
        f"5M {confirm_type} | 4H={b4h} 1H={b1h} | "
        f"sess={sessions} | spread={spread_pips}pip netRR={net_rr} | "
        f"score={total_score}/115"
    )

    return {
        "trade":       True,
        "type":        trade_type,
        "confidence":  total_score,
        "strategy":    "ICT OB/FVG Zone Reaction",
        "reason":      reason,
        "entry":       price,
        "sl":          sl,
        "tp":          tp,
        "rr":          rr,
        "net_rr":      net_rr,
        "spread_pips": spread_pips,
        "total_cost_pips":  total_cost_pips,
    }