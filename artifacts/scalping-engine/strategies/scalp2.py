"""
Strategy 2 — Liquidity Sweep Reversal Scalping
===============================================
Exploit liquidity grabs (fake breakouts) where price sweeps previous
highs/lows, traps traders, then reverses.

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

Scoring (max 125):
  Sweep quality    — 25 (CHoCH) or 10 (BOS) — BOS+BOS combo rejected
  Reversal confirm — 25 (5M CHoCH) or 10 (5M BOS)
  Market condition — 15 (ranging) or 5 (slight trend)
  Entry precision  — 15 (≤5p) / 10 (≤15p) / 5 (>15p from sweep)
  Zone confluence  — 10
  Session timing   — 10
  Freshness bonus  — 10 (<2h) / 5 (2–4h) / 0 (>4h)

  Minimum to fire  : 80
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

    now_sec = int(_time.time())

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

    # ── Step 2: Sweep detection on 15M — tightened staleness windows ─────
    # CHoCH sweeps: max 6h (high-quality structural event, can age slightly)
    # BOS sweeps  : max 2h (lower quality — must be very fresh to be valid)
    # Old value was 24h, which was the root cause of most stale-sweep losses.
    CHOCH_SWEEP_MAX_AGE = 6 * 3600
    BOS_SWEEP_MAX_AGE   = 2 * 3600

    bos_15m   = s15m.get("bos",   [])
    choch_15m = s15m.get("choch", [])

    bearish_choch = _best_sweep(choch_15m, "direction", "bearish", CHOCH_SWEEP_MAX_AGE)
    bearish_bos   = _best_sweep(bos_15m,   "direction", "bearish", BOS_SWEEP_MAX_AGE)
    bullish_choch = _best_sweep(choch_15m, "direction", "bullish", CHOCH_SWEEP_MAX_AGE)
    bullish_bos   = _best_sweep(bos_15m,   "direction", "bullish", BOS_SWEEP_MAX_AGE)

    def _pick(choch_item, bos_item):
        if choch_item and bos_item:
            if abs(bos_item.get("time", 0) - choch_item.get("time", 0)) <= 2 * 3600:
                return choch_item
            return choch_item # CHoCH always preferred over BOS when both are valid
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

    # ── Step 3: Verify reversal — minimum recovery check ─────────────────
    pip           = config.get_symbol_cfg(state.get("symbol"))["pip_size"]
    near_pips     = config.NEAR_LEVEL_PIPS
    base_recovery = config.MIN_SWEEP_RECOVERY_PIPS * pip   # 5 pips (CHOCH sweeps)
    bos_recovery  = 7 * pip                                 # 7 pips (BOS sweeps — tighter)

    direction   = None
    trade_type  = None
    sweep_score = 0
    sweep_level = None
    sweep_item  = None
    is_choch_sweep = False

    # Determine required recovery based on sweep quality
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

    # ── Step 4: 5M reversal confirmation — within 1 hour ─────────────────
    bos_5m   = s5m.get("bos",   [])
    choch_5m = s5m.get("choch", [])

    # 5M confirmation window tightened from 2h → 1h.
    # A CHoCH/BOS from 90+ minutes ago is no longer a valid trigger.
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
    elif conf_bos:
        reversal_score   = 10
        is_choch_confirm = False
    else:
        if debug: print(f"    [S2] skip: no {direction} CHoCH/BOS on 5M within 1h")
        return None

    # ── Quality gate: reject BOS sweep + BOS confirmation (lowest grade) ─
    if not is_choch_sweep and not is_choch_confirm:
        if debug: print("    [S2] skip: BOS sweep + BOS confirm only — need at least one CHoCH")
        return None

    # ── Step 5: Entry precision ───────────────────────────────────────────
    dist_from_sweep = abs(price - sweep_level)
    dist_pips_sw    = dist_from_sweep / pip

    if dist_pips_sw > 25:
        if debug: print(f"    [S2] skip: {dist_pips_sw:.1f}p from sweep (>25p)")
        return None

    if dist_pips_sw <= 5:
        precision_score = 15
    elif dist_pips_sw <= 15:
        precision_score = 10
    else:
        precision_score = 5

    # ── Hard gate: stale sweep + far entry = always reject ────────────────
    # A sweep older than 4h with an entry more than 15p away is double-weak.
    # The institutional intent behind the sweep has likely already expired.
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

    # BOS sweeps (lower quality) require an active session
    if not is_choch_sweep and not in_active_session:
        if debug: print("    [S2] skip: BOS sweep outside London/NY — CHOCH sweeps only in Asia")
        return None

    session_score = 10 if in_active_session else 0

    # ── Step 8: Sweep freshness bonus (tiered) ────────────────────────────
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

    if debug and freshness_bonus:
        print(f"    [S2] fresh sweep ({sweep_age_secs//3600}h old) → +{freshness_bonus} bonus")

    # ── Total score ───────────────────────────────────────────────────────
    total_score = (sweep_score + reversal_score + market_score +
                   precision_score + zone_score + session_score + freshness_bonus + htf_bonus + sr_bonus)

    if debug:
        print(f"    [S2] {direction} | sweep={sweep_score}({'CHoCH' if is_choch_sweep else 'BOS'}) "
              f"rev={reversal_score}({'CHoCH' if is_choch_confirm else 'BOS'}) "
              f"mkt={market_score} prec={precision_score} "
              f"zone={zone_score} sess={session_score} fresh={freshness_bonus} "
              f"htf={htf_bonus} sr={sr_bonus} → {total_score}")

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
    tp      = (price + sl_dist * config.TARGET_RR) if direction == "bullish" \
              else (price - sl_dist * config.TARGET_RR)
    rr      = round(config.TARGET_RR, 2)
    sl      = round(sl, 5)
    tp      = round(tp, 5)

    # Use TOTAL cost (spread + Nexus commission) not just spread alone.
    # Commission values per pair are configured in config.SYMBOL_CONFIG.
    total_cost_pips = config.get_total_cost_pips(state.get("symbol"))
    spread_pips     = config.get_spread_pips(state.get("symbol"))   # kept for logging
    cost_amount     = total_cost_pips * pip
    net_tp_dist     = max(abs(tp - price) - cost_amount, 0.0)
    net_sl_dist     = sl_dist + cost_amount
    net_rr          = round(net_tp_dist / net_sl_dist, 2) if net_sl_dist > 0 else 0

    # ── Post filters ──────────────────────────────────────────────────────
    if dist_pips_sw > 25:
        print(f"    [S2] REJECTED: {dist_pips_sw:.1f}p from sweep (>25p hard limit)")
        return None

    candles_5m_raw = s5m.get("candles", [])
    body_threshold = 0.50 if is_choch_confirm else 0.70
    reversal_ok    = False
    for c in reversed(candles_5m_raw[-12:]):
        o_  = c.get("open",  0); h_ = c.get("high",  0)
        l_  = c.get("low",   0); cl_= c.get("close", 0)
        if (cl_ > o_) if direction == "bullish" else (cl_ < o_):
            rng = h_ - l_; body = abs(cl_ - o_)
            if rng > 0 and (body / rng) >= body_threshold:
                reversal_ok = True; break

    if not reversal_ok:
        qual = f"CHoCH body≥50%" if is_choch_confirm else "BOS body≥70%"
        print(f"    [S2] REJECTED: no strong reversal candle ({qual} required)")
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

    sweep_type    = "CHoCH" if is_choch_sweep   else "BOS"
    reversal_type = "CHoCH" if is_choch_confirm else "BOS"
    mkt_desc      = "range" if market_score == 15 else "slight-trend"
    sweep_age_h   = round(sweep_age_secs / 3600, 1)

    reason = (
        f"15M sweep={direction}({sweep_type}) @ {sweep_level:.5f} age={sweep_age_h}h | "
        f"5M confirm={reversal_type} | mkt={mkt_desc} | "
        f"dist={dist_pips_sw:.1f}p prec={precision_score}pts | "
        f"zone={'✓' if zone_ok else '✗'} fresh={'✓' if freshness_bonus else '✗'} "
        f"sess={sessions} | "
        f"spread={spread_pips}p+comm={total_cost_pips - spread_pips}p=cost={total_cost_pips}p netRR={net_rr} score={total_score}/125"
    )

    return {
        "trade":            True,
        "type":             trade_type,
        "confidence":       total_score,
        "strategy":         "Liquidity Sweep Reversal Scalping",
        "reason":           reason,
        "entry":            price,
        "sl":               sl,
        "tp":               tp,
        "rr":               rr,
        "net_rr":           net_rr,
        "spread_pips":      spread_pips,
        "total_cost_pips":  total_cost_pips,
    }