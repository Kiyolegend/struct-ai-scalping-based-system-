"""
Strategy 2 — Liquidity Sweep Reversal Scalping
===============================================
Exploit liquidity grabs (fake breakouts) where price sweeps previous
highs/lows, traps traders, then reverses.

Quality upgrades applied (v2):
  - GATE: Sweep wick must reach ≥3 pips beyond sweep level (real grab, not a touch).
  - GATE: Post-reversal structure must hold — candles after CHoCH cannot make a new
    low (bullish) or new high (bearish) below/above the first post-CHoCH close.
  - GATE: BOS sweep entry tightened from 25p → 20p (CHoCH sweeps keep 25p).
  - BONUS: FVG at sweep level +10 pts (institutional order imbalance at exact level).
  - BONUS: 4H Order Block at sweep level +10 pts (macro institutional intent).

Tightening applied (post live trading review):
  - BOS + BOS combo rejected — at least one CHoCH required.
  - CHoCH sweeps: max age 6h. BOS sweeps: max age 2h.
    (Was 24h — root cause of most stale-sweep losses.)
  - 5M confirmation window tightened from 2h → 1h.
  - Hard reject: sweep older than 4h AND entry >15p from sweep level.
    (Stale + far = double-weak, always reject.)
  - Freshness bonus tiered: <2h = +10pts, 2-4h = +5pts, >4h = 0.
  - Net RR now uses total cost (spread + Nexus commission pip gap).
  - BOS sweeps require London/NY session.
  - BOS sweeps require 7-pip minimum recovery (CHoCH = 5 pip).

Scoring (max 145):
  Sweep quality    — 25 (CHoCH) or 10 (BOS)
  Reversal confirm — 25 (5M CHoCH) or 10 (5M BOS)
  Market condition — 15 (ranging) or 5 (slight trend)
  Entry precision  — 15 (≤5p) / 10 (≤15p) / 5 (>15p from sweep)
  Zone confluence  — 10
  Session timing   — 10
  Freshness bonus  — 10 (<2h) / 5 (2–4h) / 0 (>4h)
  HTF alignment    — up to 10 (5 per TF)
  SR level bonus   — 5
  FVG at level     — 10  ← NEW
  4H OB at level   — 10  ← NEW

  Minimum to fire  : 88 (S2 local min, up from global 80 — see note at score check)
"""

import sys, os, math, time as _time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config


def _best_sweep(items: list, key: str, value: str, max_age_secs: int, now_sec: int = 0) -> dict | None:
    """Return the most recent item where item[key]==value that is not stale."""
    now = now_sec or int(_time.time())
    candidates = [
        item for item in items
        if isinstance(item, dict)
        and item.get(key) == value
        and (now - item.get("time", 0)) <= max_age_secs
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.get("time", 0))


# ── NEW: Fair Value Gap at sweep level ────────────────────────────────────────
def _fvg_at_level(candles: list, level: float, direction: str,
                  pip: float, threshold_pips: float = 10.0) -> bool:
    """
    Returns True if there is an unmitigated FVG within threshold_pips of `level`
    in the given direction.
      Bullish FVG: candle[i-1].high < candle[i+1].low (gap up, price moved fast up)
      Bearish FVG: candle[i-1].low  > candle[i+1].high (gap down, price moved fast down)
    Mitigated if any subsequent candle closes back into the gap.
    """
    n         = len(candles)
    threshold = threshold_pips * pip
    if n < 3:
        return False

    for i in range(1, n - 1):
        prev  = candles[i - 1]
        next_ = candles[i + 1]

        if direction == "bullish":
            gap_top    = next_["low"]
            gap_bottom = prev["high"]
            if gap_top > gap_bottom and (gap_top - gap_bottom) >= 3 * pip:
                center = (gap_top + gap_bottom) / 2
                if abs(center - level) <= threshold:
                    mitigated = any(fc["close"] <= gap_bottom for fc in candles[i + 2:])
                    if not mitigated:
                        return True

        elif direction == "bearish":
            gap_top    = prev["low"]
            gap_bottom = next_["high"]
            if gap_top > gap_bottom and (gap_top - gap_bottom) >= 3 * pip:
                center = (gap_top + gap_bottom) / 2
                if abs(center - level) <= threshold:
                    mitigated = any(fc["close"] >= gap_top for fc in candles[i + 2:])
                    if not mitigated:
                        return True

    return False


# ── NEW: 4H Order Block near sweep level ──────────────────────────────────────
def _ob4h_at_level(candles_4h: list, level: float, direction: str,
                   pip: float, threshold_pips: float = 10.0) -> bool:
    """
    Returns True if there is an unmitigated 4H Order Block within threshold_pips
    of `level` in the given direction.
      Bullish OB: bearish 4H candle followed by an impulsive break upward.
      Bearish OB: bullish 4H candle followed by an impulsive break downward.
    Mitigation: any subsequent candle closes clearly beyond the OB boundary (3-pip buffer).
    """
    n         = len(candles_4h)
    threshold = threshold_pips * pip
    min_size  = 5 * pip
    if n < 6:
        return False

    for i in range(1, n - 3):
        c        = candles_4h[i]
        lookback = candles_4h[max(0, i - 8):i]
        avg_rng  = (
            sum(x["high"] - x["low"] for x in lookback) / len(lookback)
            if lookback else 0
        )
        if avg_rng == 0:
            continue

        fwd = candles_4h[i + 1: min(i + 5, n)]

        if direction == "bullish" and c["close"] < c["open"]:
            future_high = max((x["close"] for x in fwd), default=0)
            if future_high > c["high"] and (c["high"] - c["low"]) >= min_size:
                brk = max(fwd, key=lambda x: x["high"] - x["low"], default=None)
                if brk and (brk["high"] - brk["low"]) >= 1.5 * avg_rng:
                    center = (c["high"] + c["low"]) / 2
                    if abs(center - level) <= threshold:
                        ob_mid = (c["open"] + c["close"]) / 2
                        if not any(fc["close"] < ob_mid for fc in candles_4h[i + 1:]):
                            return True

        elif direction == "bearish" and c["close"] > c["open"]:
            future_low = min((x["close"] for x in fwd), default=float("inf"))
            if future_low < c["low"] and (c["high"] - c["low"]) >= min_size:
                brk = max(fwd, key=lambda x: x["high"] - x["low"], default=None)
                if brk and (brk["high"] - brk["low"]) >= 1.5 * avg_rng:
                    center = (c["high"] + c["low"]) / 2
                    if abs(center - level) <= threshold:
                        ob_mid = (c["open"] + c["close"]) / 2
                        if not any(fc["close"] > ob_mid for fc in candles_4h[i + 1:]):
                            return True

    return False


# ── NEW: Post-reversal structure holds ────────────────────────────────────────
def _structure_holds(candles_5m: list, choch_time: int, direction: str) -> bool:
    """
    After the CHoCH timestamp, subsequent 5M candles must not violate the
    reversal by making a new low (bullish) or new high (bearish) below/above
    the close of the very first candle after the CHoCH.

    Returns True if structure is intact or there is not enough data to judge.
    """
    post = [c for c in candles_5m if c.get("time", 0) > choch_time]
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
    s5m   = state.get("5m",  {})
    s15m  = state.get("15m", {})
    s4h   = state.get("4h",  {})

    if not price or not isinstance(price, (int, float)) or not math.isfinite(price):
        return None

    now_sec = int(state.get("reference_ts") or _time.time())

    # ── Step 1: Market condition — avoid strong trends ────────────────────
    b4h = bias.get("4h", "neutral")
    b1h = bias.get("1h", "neutral")

    strongly_trending = (b4h == b1h) and b4h not in ("neutral", "")
    slightly_trending = (
        b4h not in ("neutral", "") or b1h not in ("neutral", "")
    ) and not strongly_trending

    if strongly_trending:
        if debug: print("    [S2] skip: strongly trending market — use S1")
        return None

    market_score = 15 if not slightly_trending else 5

    # ── Step 2: Sweep detection on 15M ───────────────────────────────────
    CHOCH_SWEEP_MAX_AGE = 6 * 3600
    BOS_SWEEP_MAX_AGE   = 2 * 3600

    bos_15m   = s15m.get("bos",   [])
    choch_15m = s15m.get("choch", [])

    bearish_choch = _best_sweep(choch_15m, "direction", "bearish", CHOCH_SWEEP_MAX_AGE, now_sec=now_sec)
    bearish_bos   = _best_sweep(bos_15m,   "direction", "bearish", BOS_SWEEP_MAX_AGE,   now_sec=now_sec)
    bullish_choch = _best_sweep(choch_15m, "direction", "bullish", CHOCH_SWEEP_MAX_AGE, now_sec=now_sec)
    bullish_bos   = _best_sweep(bos_15m,   "direction", "bullish", BOS_SWEEP_MAX_AGE,   now_sec=now_sec)

    def _pick(choch_item, bos_item):
        if choch_item and bos_item:
           return choch_item if choch_item.get("time", 0) >= bos_item.get("time", 0) else bos_item
        return choch_item or bos_item

    buy_sweep_item  = _pick(bearish_choch, bearish_bos)
    sell_sweep_item = _pick(bullish_choch, bullish_bos)

    def _sweep_score(item, choch_list):
        if item is None:
            return 0
        return 25 if item in choch_list else 10

    buy_sweep_score  = _sweep_score(buy_sweep_item,  choch_15m)
    sell_sweep_score = _sweep_score(sell_sweep_item, choch_15m)

    buy_sweep_price  = buy_sweep_item.get("price")  if buy_sweep_item  else None
    sell_sweep_price = sell_sweep_item.get("price") if sell_sweep_item else None

    # ── Step 3: Recovery check ────────────────────────────────────────────
    pip           = config.get_symbol_cfg(state.get("symbol"))["pip_size"]
    near_pips     = config.NEAR_LEVEL_PIPS
    base_recovery = config.MIN_SWEEP_RECOVERY_PIPS * pip
    bos_recovery  = 7 * pip

    direction      = None
    trade_type     = None
    sweep_score    = 0
    sweep_level    = None
    sweep_item     = None
    is_choch_sweep = False

    def _min_recovery(score):
        return base_recovery if score == 25 else bos_recovery

    if buy_sweep_price is not None:
        rec = _min_recovery(buy_sweep_score)
        if (price - buy_sweep_price) >= rec:
            direction      = "bullish"
            trade_type     = "BUY"
            sweep_score    = buy_sweep_score
            sweep_level    = buy_sweep_price
            sweep_item     = buy_sweep_item
            is_choch_sweep = (buy_sweep_score == 25)

    if sell_sweep_price is not None:
        rec = _min_recovery(sell_sweep_score)
        if (sell_sweep_price - price) >= rec:
            if direction is None or sell_sweep_score >= sweep_score:
                direction      = "bearish"
                trade_type     = "SELL"
                sweep_score    = sell_sweep_score
                sweep_level    = sell_sweep_price
                sweep_item     = sell_sweep_item
                is_choch_sweep = (sell_sweep_score == 25)

    if direction is None:
        if debug: print("    [S2] skip: no valid sweep with sufficient recovery")
        return None

    # ── NEW GATE 1: Sweep wick depth ──────────────────────────────────────
    # The wick must have reached at least 3 pips BEYOND the sweep level.
    # A 1-pip marginal touch is noise, not a real liquidity grab.
    # Only checked when wick_extreme is available from the API.
    wick_extreme      = sweep_item.get("wick_extreme")
    MIN_WICK_DEPTH_P  = 3
    if wick_extreme is not None:
        wick_depth_pips = abs(wick_extreme - sweep_level) / pip
        if wick_depth_pips < MIN_WICK_DEPTH_P:
            if debug:
                print(f"    [S2] skip: wick depth {wick_depth_pips:.1f}p < {MIN_WICK_DEPTH_P}p "
                      f"— not a real liquidity grab")
            return None

    # ── Step 4: 5M reversal confirmation ─────────────────────────────────
    bos_5m      = s5m.get("bos",     [])
    choch_5m    = s5m.get("choch",   [])
    candles_5m  = s5m.get("candles", [])
    CONFIRM_MAX_AGE = 1 * 3600

    conf_choch = next(
        (c for c in sorted(choch_5m, key=lambda x: x.get("time", 0), reverse=True)
         if isinstance(c, dict) and c.get("direction") == direction
         and (now_sec - c.get("time", 0)) <= CONFIRM_MAX_AGE), None
    )
    conf_bos = next(
        (b for b in sorted(bos_5m, key=lambda x: x.get("time", 0), reverse=True)
         if isinstance(b, dict) and b.get("direction") == direction
         and (now_sec - b.get("time", 0)) <= CONFIRM_MAX_AGE), None
    )

    if conf_choch:
        reversal_score   = 25
        is_choch_confirm = True
        confirm_event    = conf_choch
    elif conf_bos:
        reversal_score   = 10
        is_choch_confirm = False
        confirm_event    = conf_bos
    else:
        if debug: print(f"    [S2] skip: no {direction} CHoCH/BOS on 5M within 1h")
        return None

    if not is_choch_sweep and not is_choch_confirm:
        if debug: print("    [S2] skip: BOS sweep + BOS confirm only — need at least one CHoCH")
        return None

    # ── NEW GATE 2: Post-reversal structure must hold ─────────────────────
    # After the CHoCH fired, price must NOT have already violated the reversal.
    # Catches cases where CHoCH was real but has since been invalidated.
    choch_time = confirm_event.get("time", 0) if confirm_event else 0
    if choch_time > 0 and candles_5m:
        if not _structure_holds(candles_5m, choch_time, direction):
            if debug:
                print(f"    [S2] skip: reversal structure broke after CHoCH "
                      f"— {direction} structure already violated, waiting for next setup")
            return None

    # ── Step 5: Entry precision ───────────────────────────────────────────
    dist_from_sweep = abs(price - sweep_level)
    dist_pips_sw    = dist_from_sweep / pip

    # ── NEW GATE 3: Tighter entry limit for BOS sweeps ───────────────────
    # CHoCH sweeps (higher quality) keep the existing 25p limit.
    # BOS sweeps are inherently lower quality — entry must be tighter (20p).
    entry_limit = 25 if is_choch_sweep else 20
    if dist_pips_sw > entry_limit:
        if debug:
            lbl = "CHoCH" if is_choch_sweep else "BOS"
            print(f"    [S2] skip: {dist_pips_sw:.1f}p from sweep "
                  f"(>{entry_limit}p limit for {lbl} sweep)")
        return None

    if dist_pips_sw <= 5:
        precision_score = 15
    elif dist_pips_sw <= 15:
        precision_score = 10
    else:
        precision_score = 5

    sweep_age_secs_early = now_sec - sweep_item.get("time", now_sec) if sweep_item else 99999
    if sweep_age_secs_early > 4 * 3600 and dist_pips_sw > 15:
        if debug:
            print(f"    [S2] skip: stale sweep ({sweep_age_secs_early//3600}h) "
                  f"+ far entry ({dist_pips_sw:.1f}p) — double-weak setup rejected")
        return None

    # ── Step 6: Zone confluence ───────────────────────────────────────────
    zones_5m  = s5m.get("zones")  or []
    zones_15m = s15m.get("zones") or []
    if not isinstance(zones_5m,  list): zones_5m  = []
    if not isinstance(zones_15m, list): zones_15m = []
    threshold = near_pips * pip

    zone_ok = False
    for zone in zones_5m + zones_15m:
        if not isinstance(zone, dict): continue
        top    = zone.get("top")    or 0
        bottom = zone.get("bottom") or 0
        if top == 0 and bottom == 0: continue
        center = zone.get("center", (top + bottom) / 2)
        if not ((bottom - threshold) <= sweep_level <= (top + threshold)): continue
        if direction == "bullish" and sweep_level <= center:
            zone_ok = True; break
        if direction == "bearish" and sweep_level >= center:
            zone_ok = True; break

    zone_score = 10 if zone_ok else 0

    # ── Step 7: Session timing + BOS sweep session gate ──────────────────
    sessions       = state.get("sessions", [])
    sessions_lower = [s.lower() for s in sessions]
    in_active_session = any(s in sessions_lower for s in ["london", "ny", "new york"])

    if not is_choch_sweep and not in_active_session:
        if debug: print("    [S2] skip: BOS sweep outside London/NY — CHoCH sweeps only in Asia")
        return None

    session_score = 10 if in_active_session else 0

    # ── Step 8: Freshness bonus ───────────────────────────────────────────
    sweep_age_secs = now_sec - sweep_item.get("time", now_sec) if sweep_item else 99999
    if sweep_age_secs <= 2 * 3600:
        freshness_bonus = 10
    elif sweep_age_secs <= 4 * 3600:
        freshness_bonus = 5
    else:
        freshness_bonus = 0

    # ── Step 8b: HTF directional alignment bonus ──────────────────────────
    htf_bonus = 0
    if direction == "bullish":
        if b1h == "bullish": htf_bonus += 5
        if b4h == "bullish": htf_bonus += 5
    elif direction == "bearish":
        if b1h == "bearish": htf_bonus += 5
        if b4h == "bearish": htf_bonus += 5

    # ── Step 8c: S/R level proximity bonus ───────────────────────────────
    sr_bonus     = 0
    sr_levels    = state.get("sr_levels", []) or []
    sr_threshold = 10 * pip
    for level in sr_levels:
        if not isinstance(level, dict): continue
        lp = level.get("price")
        if lp and abs(sweep_level - lp) <= sr_threshold:
            sr_bonus = 5
            break

    # ── NEW BONUS 1: FVG at sweep level (+10) ────────────────────────────
    # A Fair Value Gap at the sweep level indicates an unmitigated imbalance
    # right at the point where liquidity was grabbed — institutional intent.
    fvg_bonus = 0
    fvg_label = ""
    if candles_5m:
        if _fvg_at_level(candles_5m, sweep_level, direction, pip, threshold_pips=10.0):
            fvg_bonus = 10
            fvg_label = "+FVG"
            if debug: print(f"    [S2] FVG found at sweep level {sweep_level:.5f} → +10")

    # ── NEW BONUS 2: 4H Order Block at sweep level (+10) ─────────────────
    # A 4H OB coinciding with the sweep level means institutional players
    # placed orders at exactly this level. The sweep itself may have been
    # engineered to collect liquidity resting at this OB before reversing.
    ob4h_bonus = 0
    ob4h_label = ""
    candles_4h = s4h.get("candles", [])
    if candles_4h:
        if _ob4h_at_level(candles_4h, sweep_level, direction, pip, threshold_pips=10.0):
            ob4h_bonus = 10
            ob4h_label = "+4H-OB"
            if debug: print(f"    [S2] 4H OB found at sweep level {sweep_level:.5f} → +10")

    # ── Total score ───────────────────────────────────────────────────────
    total_score = (
        sweep_score + reversal_score + market_score +
        precision_score + zone_score + session_score +
        freshness_bonus + htf_bonus + sr_bonus +
        fvg_bonus + ob4h_bonus
    )

    if debug:
        print(f"    [S2] {direction} | "
              f"sweep={sweep_score}({'CHoCH' if is_choch_sweep else 'BOS'}) "
              f"rev={reversal_score}({'CHoCH' if is_choch_confirm else 'BOS'}) "
              f"mkt={market_score} prec={precision_score} "
              f"zone={zone_score} sess={session_score} fresh={freshness_bonus} "
              f"htf={htf_bonus} sr={sr_bonus} "
              f"fvg={fvg_bonus} ob4h={ob4h_bonus} → {total_score}")

    # S2-local minimum: max score grew from 125 → 145 with the v2 upgrades.
    # Keeping global MIN_CONFIDENCE=80 as the floor would make 80/145=55%
    # proportionally easier to hit than the old 80/125=64%.
    # S2_MIN_SCORE=88 restores equivalent selectivity (88/145=60.7% ≈ old 64%).
    S2_MIN_SCORE = max(config.MIN_CONFIDENCE, 88)
    if total_score < S2_MIN_SCORE:
        if debug: print(f"    [S2] skip: score {total_score} < {S2_MIN_SCORE} (S2 local min)")
        return None

    # ── SL / TP ───────────────────────────────────────────────────────────
    buf = config.SWEEP_SL_BUFFER_PIPS * pip
    if direction == "bullish":
        sl_anchor = wick_extreme if wick_extreme is not None else sweep_level
        sl        = sl_anchor - buf
        if sl >= price:
            if debug: print("    [S2] skip: SL not below entry for BUY")
            return None
    else:
        sl_anchor = wick_extreme if wick_extreme is not None else sweep_level
        sl        = sl_anchor + buf
        if sl <= price:
            if debug: print("    [S2] skip: SL not above entry for SELL")
            return None

    sl_dist = abs(price - sl)
    _fib = config.fib_extension_tp(state, direction, price)
    tp   = _fib if _fib is not None else (
               (price + sl_dist * config.TARGET_RR) if direction == "bullish"
               else (price - sl_dist * config.TARGET_RR))
    rr      = round(config.TARGET_RR, 2)
    sl      = round(sl, 5)
    tp      = round(tp, 5)

    total_cost_pips = config.get_total_cost_pips(state.get("symbol"))
    spread_pips     = config.get_spread_pips(state.get("symbol"))
    cost_amount     = total_cost_pips * pip
    net_tp_dist     = max(abs(tp - price) - cost_amount, 0.0)
    net_sl_dist     = sl_dist + cost_amount
    net_rr          = round(net_tp_dist / net_sl_dist, 2) if net_sl_dist > 0 else 0

    # ── Post filters ──────────────────────────────────────────────────────
    body_threshold = 0.50 if is_choch_confirm else 0.70
    reversal_ok    = False
    for c in reversed(candles_5m[-12:]):
        o_  = c.get("open",  0)
        h_  = c.get("high",  0)
        l_  = c.get("low",   0)
        cl_ = c.get("close", 0)
        if (cl_ > o_) if direction == "bullish" else (cl_ < o_):
            rng = h_ - l_; body = abs(cl_ - o_)
            if rng > 0 and (body / rng) >= body_threshold:
                reversal_ok = True; break

    if not reversal_ok:
        qual = "CHoCH body≥50%" if is_choch_confirm else "BOS body≥70%"
        print(f"    [S2] REJECTED: no strong reversal candle ({qual} required)")
        return None

    if sl_dist < config.MIN_SL_PIPS * pip:
        print(f"    [S2] REJECTED: SL too tight ({sl_dist/pip:.1f}p < {config.MIN_SL_PIPS})")
        return None

    if abs(tp - price) / sl_dist < 1.5:
        print(f"    [S2] REJECTED: raw RR < 1.5")
        return None

    if net_rr < config.NET_MIN_RR:
        print(f"    [S2] REJECTED: net RR {net_rr} < {config.NET_MIN_RR}")
        return None

    sweep_type    = "CHoCH" if is_choch_sweep   else "BOS"
    reversal_type = "CHoCH" if is_choch_confirm else "BOS"
    mkt_desc      = "range" if market_score == 15 else "slight-trend"
    sweep_age_h   = round(sweep_age_secs / 3600, 1)
    new_conf      = " ".join(filter(None, [fvg_label, ob4h_label]))

    reason = (
        f"15M sweep={direction}({sweep_type}) @ {sweep_level:.5f} age={sweep_age_h}h | "
        f"5M confirm={reversal_type} | mkt={mkt_desc} | "
        f"dist={dist_pips_sw:.1f}p prec={precision_score}pts | "
        f"zone={'✓' if zone_ok else '✗'} fresh={'✓' if freshness_bonus else '✗'} "
        f"{new_conf + ' | ' if new_conf else ''}"
        f"sess={sessions} | "
        f"spread={spread_pips}p+comm={total_cost_pips - spread_pips}p"
        f"=cost={total_cost_pips}p netRR={net_rr} score={total_score}/145"
    )

    return {
        "trade":           True,
        "type":            trade_type,
        "confidence":      total_score,
        "strategy":        "Liquidity Sweep Reversal Scalping",
        "reason":          reason,
        "entry":           price,
        "sl":              sl,
        "tp":              tp,
        "rr":              rr,
        "net_rr":          net_rr,
        "spread_pips":     spread_pips,
        "total_cost_pips": total_cost_pips,
    }