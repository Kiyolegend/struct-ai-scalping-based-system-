"""
Strategy Logic Testing Engine
==============================
Validates Strategy 1 and Strategy 2 using 8 deterministic market scenarios.
Tests: trigger conditions, scoring accuracy, selection logic, SL/TP correctness.

No live data. No randomness. No backtesting.
All inputs simulate realistic STRUCT.ai API outputs.

Run with: python3 tests/test_scenarios.py
Run with debug: python3 tests/test_scenarios.py --debug
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DEBUG = "--debug" in sys.argv

from strategies.scalp1 import check as strat1
from strategies.scalp2 import check as strat2
import config

SCORE_THRESHOLD = 70


# ── Mock state builder ────────────────────────────────────────────────────────

def _s(label, price, kind="high"):
    return {"label": label, "price": price, "kind": kind}

def _bos(direction, price):
    return {"direction": direction, "price": price}

def _choch(direction, price):
    return {"direction": direction, "price": price}

def _zone(top, bottom):
    return {"top": top, "bottom": bottom, "center": (top + bottom) / 2}

def build(
    price=154.500,
    b4h="neutral", b1h="neutral", b15m="neutral",
    struct_15m=None, bos_15m=None, choch_15m=None, zones_15m=None,
    struct_5m=None,  bos_5m=None,  choch_5m=None,  zones_5m=None,
    sr_levels=None, sessions=None,
):
    return {
        "current_price":    price,
        "bias":             {"4h": b4h, "1h": b1h, "15m": b15m},
        "sessions":         sessions or ["london"],
        "tradeable_session": True,
        "sr_levels":        sr_levels or [],
        "asia_range":       {"high": price + 0.300, "low": price - 0.300},
        "15m": {
            "trend":     b15m,
            "structure": struct_15m or [],
            "bos":       bos_15m   or [],
            "choch":     choch_15m or [],
            "zones":     zones_15m or [],
            "candles":   [],
        },
        "5m": {
            "trend":     b15m,
            "structure": struct_5m  or [],
            "bos":       bos_5m    or [],
            "choch":     choch_5m  or [],
            "zones":     zones_5m  or [],
            "candles":   [],
        },
        "1m":  {"trend": "neutral", "structure": [], "bos": [], "choch": [], "zones": [], "candles": [], "sr_levels": []},
        "1h":  {"trend": b1h, "structure": [], "bos": [], "choch": [], "zones": [], "candles": [], "sr_levels": []},
    }


# ── Selection logic ───────────────────────────────────────────────────────────

def select_strategy(r1, r2):
    """Pick the highest-scoring signal above threshold. Returns (selected_result, selected_name)."""
    s1_score = r1.get("confidence", 0) if r1 else 0
    s2_score = r2.get("confidence", 0) if r2 else 0

    if s1_score >= SCORE_THRESHOLD and s2_score >= SCORE_THRESHOLD:
        if s1_score >= s2_score:
            return r1, "Strategy 1"
        else:
            return r2, "Strategy 2"
    elif s1_score >= SCORE_THRESHOLD:
        return r1, "Strategy 1"
    elif s2_score >= SCORE_THRESHOLD:
        return r2, "Strategy 2"
    else:
        return None, "NO TRADE"


# ── Test runner ───────────────────────────────────────────────────────────────

def run_scenario(num, market_type, state, notes, expect):
    r1 = strat1(state, debug=DEBUG)
    r2 = strat2(state, debug=DEBUG)

    selected_result, selected_name = select_strategy(r1, r2)

    s1_info = {
        "triggered": r1 is not None,
        "score":     r1.get("confidence", 0) if r1 else 0,
        "direction": r1.get("type", "—") if r1 else "—",
        "reason":    r1.get("reason", "Conditions not met") if r1 else "Conditions not met",
    }
    s2_info = {
        "triggered": r2 is not None,
        "score":     r2.get("confidence", 0) if r2 else 0,
        "direction": r2.get("type", "—") if r2 else "—",
        "reason":    r2.get("reason", "Conditions not met") if r2 else "Conditions not met",
    }

    output = {
        "test_case":         num,
        "market_type":       market_type,
        "strategy_1":        s1_info,
        "strategy_2":        s2_info,
        "selected_strategy": selected_name,
        "entry":             selected_result.get("entry") if selected_result else None,
        "sl":                selected_result.get("sl")    if selected_result else None,
        "tp":                selected_result.get("tp")    if selected_result else None,
        "notes":             notes,
    }

    # Validation
    passed = selected_name == expect
    status = "✓ PASS" if passed else "✗ FAIL"
    marker = "  " + status

    print(f"\n{'═'*68}")
    print(f"  TEST {num:02d} | {market_type}")
    print(f"{'─'*68}")
    print(f"  Strategy 1  │ triggered={s1_info['triggered']}  score={s1_info['score']}  dir={s1_info['direction']}")
    if DEBUG or not s1_info["triggered"]:
        short_r = s1_info["reason"][:80] + "..." if len(s1_info["reason"]) > 80 else s1_info["reason"]
        print(f"              │ {short_r}")
    print(f"  Strategy 2  │ triggered={s2_info['triggered']}  score={s2_info['score']}  dir={s2_info['direction']}")
    if DEBUG or not s2_info["triggered"]:
        short_r = s2_info["reason"][:80] + "..." if len(s2_info["reason"]) > 80 else s2_info["reason"]
        print(f"              │ {short_r}")
    print(f"{'─'*68}")
    print(f"  Selected    │ {selected_name}")
    if selected_result:
        entry = selected_result.get("entry", 0)
        sl    = selected_result.get("sl", 0)
        tp    = selected_result.get("tp", 0)
        sl_d  = abs(entry - sl)
        tp_d  = abs(entry - tp)
        rr    = tp_d / sl_d if sl_d > 0 else 0
        print(f"  Entry/SL/TP │ entry={entry:.3f}  sl={sl:.3f}  tp={tp:.3f}  RR={rr:.1f}:1")
    print(f"  Expected    │ {expect}")
    print(f"  Notes       │ {notes}")
    print(f"{marker}")

    return passed, output


# ═══════════════════════════════════════════════════════════════════════════════
# DEFINE SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════════

scenarios = []

# ── 1. Strong Trend Bullish ───────────────────────────────────────────────────
scenarios.append({
    "num":         1,
    "market_type": "Strong Trend — Bullish",
    "notes":       "4H+1H bullish, clean HH+HL on 15M, fresh HL near price, strong 5M BOS → Strategy 1 BUY",
    "expect":      "Strategy 1",
    "state": build(
        price=154.500,
        b4h="bullish", b1h="bullish", b15m="bullish",
        struct_15m=[
            _s("HH", 154.900, "high"),
            _s("HL", 154.480, "low"),   # fresh HL — price only 20 pips away
        ],
        struct_5m=[_s("HL", 154.470, "low")],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        zones_5m=[_zone(154.510, 154.460)],
        sessions=["london"],
    ),
})

# ── 2. Strong Trend Bearish ───────────────────────────────────────────────────
scenarios.append({
    "num":         2,
    "market_type": "Strong Trend — Bearish",
    "notes":       "4H+1H bearish, clean LH+LL on 15M, fresh LH near price, strong 5M BOS → Strategy 1 SELL",
    "expect":      "Strategy 1",
    "state": build(
        price=154.500,
        b4h="bearish", b1h="bearish", b15m="bearish",
        struct_15m=[
            _s("LL", 154.100, "low"),
            _s("LH", 154.520, "high"),  # fresh LH — price only 20 pips away
        ],
        struct_5m=[_s("LH", 154.530, "high")],
        bos_5m=[_bos("bearish", 154.510), _bos("bearish", 154.505)],
        zones_5m=[_zone(154.540, 154.490)],
        sessions=["ny"],
    ),
})

# ── 3. Ranging Market — No Sweep ─────────────────────────────────────────────
scenarios.append({
    "num":         3,
    "market_type": "Ranging Market — No Sweep",
    "notes":       "4H+1H neutral, no 15M sweep, no 5M BOS/CHOCH → NO TRADE",
    "expect":      "NO TRADE",
    "state": build(
        price=154.500,
        b4h="neutral", b1h="neutral", b15m="neutral",
        struct_15m=[],
        bos_15m=[],
        choch_15m=[],
        bos_5m=[],
        choch_5m=[],
        sessions=["london"],
    ),
})

# ── 4. Liquidity Sweep High — SELL ───────────────────────────────────────────
scenarios.append({
    "num":         4,
    "market_type": "Liquidity Sweep — High Swept (SELL)",
    "notes":       "Bullish CHOCH on 15M (swept above high), bearish CHOCH on 5M confirms reversal → S2 SELL",
    "expect":      "Strategy 2",
    "state": build(
        price=154.500,
        b4h="neutral", b1h="neutral", b15m="neutral",
        choch_15m=[_choch("bullish", 154.550)],  # price swept above 154.550, now at 154.500
        choch_5m=[_choch("bearish", 154.510)],   # 5M confirms reversal
        zones_15m=[_zone(154.570, 154.530)],
        sessions=["london"],
    ),
})

# ── 5. Liquidity Sweep Low — BUY ─────────────────────────────────────────────
scenarios.append({
    "num":         5,
    "market_type": "Liquidity Sweep — Low Swept (BUY)",
    "notes":       "Bearish CHOCH on 15M (swept below low), bullish CHOCH on 5M confirms reversal → S2 BUY",
    "expect":      "Strategy 2",
    "state": build(
        price=154.500,
        b4h="neutral", b1h="neutral", b15m="neutral",
        choch_15m=[_choch("bearish", 154.450)],  # price swept below 154.450, now at 154.500
        choch_5m=[_choch("bullish", 154.490)],   # 5M confirms reversal
        zones_5m=[_zone(154.470, 154.430)],
        sessions=["ny"],
    ),
})

# ── 6. Overextended Trend — Price Too Far from 15M HL ────────────────────────
scenarios.append({
    "num":         6,
    "market_type": "Overextended Trend — Price Far from Pullback",
    "notes":       "4H+1H bullish but price is 80 pips above 15M HL (missed the pullback) → NO TRADE",
    "expect":      "NO TRADE",
    "state": build(
        price=154.500,
        b4h="bullish", b1h="bullish", b15m="bullish",
        struct_15m=[
            _s("HH", 154.900, "high"),
            _s("HL", 153.700, "low"),   # 80 pips away → overextended, hard reject
        ],
        bos_5m=[_bos("bullish", 154.490)],
        sessions=["london"],
    ),
})

# ── 7. No BOS / No Confirmation ──────────────────────────────────────────────
scenarios.append({
    "num":         7,
    "market_type": "No BOS / No Confirmation on Either TF",
    "notes":       "4H+1H bullish, 15M HL present near price, but no 5M BOS at all → NO TRADE",
    "expect":      "NO TRADE",
    "state": build(
        price=154.500,
        b4h="bullish", b1h="bullish", b15m="bullish",
        struct_15m=[
            _s("HH", 154.900, "high"),
            _s("HL", 154.490, "low"),
        ],
        bos_5m=[],      # no BOS
        choch_5m=[],    # no CHOCH
        sessions=["london"],
    ),
})

# ── 8. Conflicting Signals — Both Strategies Weak ────────────────────────────
scenarios.append({
    "num":         8,
    "market_type": "Conflicting / Ambiguous — Both Strategies Weak",
    "notes":       "Mixed bias with weak structure: S1 has partial conditions, S2 has BOS-only sweep. Both below 70 → NO TRADE",
    "expect":      "NO TRADE",
    "state": build(
        price=154.500,
        b4h="bullish", b1h="neutral",   # only one aligned — S1 gets 15pts bias, not 30
        b15m="neutral",
        struct_15m=[
            _s("HH", 154.900, "high"),
            # no HL — partial structure only
        ],
        bos_15m=[_bos("bearish", 154.450)],  # weak BOS-only sweep (not CHOCH)
        bos_5m=[_bos("bullish", 154.490)],   # only 1 BOS (weak)
        choch_5m=[],
        sessions=[],   # dead session — no session bonus
    ),
})


# ═══════════════════════════════════════════════════════════════════════════════
# RUN ALL SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "═"*68)
print("  STRUCT.ai Scalping Engine — Strategy Logic Testing Engine")
print(f"  Strategies: Strategy 1 (MTF Pullback) + Strategy 2 (Liq. Sweep)")
print(f"  Threshold:  {SCORE_THRESHOLD}/100  |  Debug: {DEBUG}")
print("═"*68)

passed_count = 0
failed_count = 0
all_outputs  = []

for sc in scenarios:
    passed, output = run_scenario(
        num=sc["num"],
        market_type=sc["market_type"],
        state=sc["state"],
        notes=sc["notes"],
        expect=sc["expect"],
    )
    all_outputs.append(output)
    if passed:
        passed_count += 1
    else:
        failed_count += 1

# ── Summary ───────────────────────────────────────────────────────────────────

print(f"\n{'═'*68}")
print(f"  SCENARIO RESULTS: {passed_count}/{len(scenarios)} correct")
print(f"{'─'*68}")

for o in all_outputs:
    expected   = scenarios[o["test_case"] - 1]["expect"]
    selected   = o["selected_strategy"]
    match      = "✓" if selected == expected else "✗"
    entry_str  = f"entry={o['entry']:.3f} sl={o['sl']:.3f} tp={o['tp']:.3f}" if o["entry"] else "no trade"
    print(f"  {match} TC{o['test_case']:02d} │ {o['market_type']:<42} → {selected}")
    if o["entry"]:
        print(f"       │ {entry_str}")

print(f"{'═'*68}")
print(f"  Passed: {passed_count}   Failed: {failed_count}")
print()

# ── Validation checks ─────────────────────────────────────────────────────────
print("  VALIDATION CHECKS")
print(f"{'─'*68}")

checks = []

# S1 only triggers in trend scenarios (1, 2)
s1_triggered_in_ranges = [o for o in all_outputs if o["strategy_1"]["triggered"] and o["test_case"] in [3, 4, 5]]
checks.append(("Strategy 1 never triggers in ranging/sweep scenarios",
                len(s1_triggered_in_ranges) == 0,
                f"{len(s1_triggered_in_ranges)} unexpected triggers"))

# S2 never triggers in strongly trending market (scenarios 1, 2)
s2_triggered_in_trend = [o for o in all_outputs if o["strategy_2"]["triggered"] and o["test_case"] in [1, 2]]
checks.append(("Strategy 2 never triggers in strongly trending market",
                len(s2_triggered_in_trend) == 0,
                f"{len(s2_triggered_in_trend)} unexpected triggers"))

# No trades in scenarios 3, 6, 7, 8
no_trade_expected = [o for o in all_outputs if o["test_case"] in [3, 6, 7, 8]]
bad_trades = [o for o in no_trade_expected if o["selected_strategy"] != "NO TRADE"]
checks.append(("No trades fired in bad-condition scenarios (3, 6, 7, 8)",
                len(bad_trades) == 0,
                f"scenarios {[o['test_case'] for o in bad_trades]} fired incorrectly"))

# All trades that fire have valid SL direction
for o in all_outputs:
    if o["entry"]:
        sel = o["selected_strategy"]
        e, sl, tp = o["entry"], o["sl"], o["tp"]
        strat_result = [s for s in scenarios if s["num"] == o["test_case"]][0]
        if sel == "Strategy 1":
            direction = "BUY" if o["strategy_1"]["direction"] == "BUY" else "SELL"
        else:
            direction = "BUY" if o["strategy_2"]["direction"] == "BUY" else "SELL"

        if direction == "BUY":
            checks.append((f"TC{o['test_case']:02d}: BUY SL below entry ({sl:.3f} < {e:.3f})",
                           sl < e, f"sl={sl:.3f} >= entry={e:.3f}"))
            checks.append((f"TC{o['test_case']:02d}: BUY TP above entry ({tp:.3f} > {e:.3f})",
                           tp > e, f"tp={tp:.3f} <= entry={e:.3f}"))
        else:
            checks.append((f"TC{o['test_case']:02d}: SELL SL above entry ({sl:.3f} > {e:.3f})",
                           sl > e, f"sl={sl:.3f} <= entry={e:.3f}"))
            checks.append((f"TC{o['test_case']:02d}: SELL TP below entry ({tp:.3f} < {e:.3f})",
                           tp < e, f"tp={tp:.3f} >= entry={e:.3f}"))

        sl_d = abs(e - sl)
        tp_d = abs(e - tp)
        rr   = tp_d / sl_d if sl_d > 0 else 0
        checks.append((f"TC{o['test_case']:02d}: RR >= 2:1 (got {rr:.2f})",
                       round(rr, 2) >= 2.0, f"rr={rr:.2f}"))

v_pass = v_fail = 0
for desc, result, detail in checks:
    if result:
        print(f"  ✓  {desc}")
        v_pass += 1
    else:
        print(f"  ✗  {desc}  [{detail}]")
        v_fail += 1

print(f"{'─'*68}")
print(f"  Validation: {v_pass} passed, {v_fail} failed")
print(f"{'═'*68}\n")

sys.exit(0 if (failed_count == 0 and v_fail == 0) else 1)
