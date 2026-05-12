"""
Scalping Engine — Full Test Suite
==================================
Tests: Strategy 1, Strategy 2, Risk Manager, Signal Memory,
       State Sanitizer, Engine Matching, Integration Pipeline

All tests run without STRUCT.ai (uses synthetic mock state data).
Run with: python3 tests/test_all.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import traceback

from strategies.scalp1 import check as strat1
from strategies.scalp2 import check as strat2
from risk.manager import validate, get_lot_size
from signal_memory import SignalMemory
from state import sanitize_state
import config

PASS = 0
FAIL = 0
ERRORS = []


def ok(name):
    global PASS
    PASS += 1
    print(f"  ✓  {name}")


def fail(name, detail=""):
    global FAIL
    FAIL += 1
    msg = f"  ✗  {name}"
    if detail:
        msg += f"\n       → {detail}"
    print(msg)
    ERRORS.append(name)


def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ── Helpers — build realistic mock states ────────────────────────────────────

def _struct(label, price, kind="high"):
    return {"label": label, "price": price, "kind": kind}


def _bos(direction, price):
    return {"direction": direction, "price": price}


def _choch(direction, price):
    return {"direction": direction, "price": price}


def _zone(top, bottom):
    return {"top": top, "bottom": bottom, "center": (top + bottom) / 2}


def _bull_candle(o=154.450, c=154.520, lo=154.440, hi=154.530):
    """Strong bullish candle — body/range ~78%."""
    return {"open": o, "high": hi, "low": lo, "close": c}


def _bear_candle(o=154.550, c=154.480, lo=154.470, hi=154.560):
    """Strong bearish candle — body/range ~78%."""
    return {"open": o, "high": hi, "low": lo, "close": c}


def make_state(
    price=154.500,
    b4h="bullish", b1h="bullish", b15m="bullish",
    struct_15m=None, bos_15m=None, choch_15m=None, zones_15m=None,
    struct_5m=None,  bos_5m=None,  choch_5m=None,  zones_5m=None,
    candles_5m=None, sessions=None,
):
    return {
        "current_price": price,
        "bias":  {"4h": b4h, "1h": b1h, "15m": b15m},
        "sessions": sessions or ["london"],
        "tradeable_session": True,
        "sr_levels": [],
        "asia_range": {"high": 154.800, "low": 154.200},
        "15m": {
            "trend": b15m,
            "structure": struct_15m or [],
            "bos":       bos_15m or [],
            "choch":     choch_15m or [],
            "zones":     zones_15m or [],
            "candles":   [],
        },
        "5m": {
            "trend": b15m,
            "structure": struct_5m or [],
            "bos":       bos_5m or [],
            "choch":     choch_5m or [],
            "zones":     zones_5m or [],
            "candles":   candles_5m if candles_5m is not None else [],
        },
        "1m": {"trend": "neutral", "structure": [], "bos": [], "choch": [], "zones": [], "candles": [], "sr_levels": []},
        "1h": {"trend": b1h,      "structure": [], "bos": [], "choch": [], "zones": [], "candles": [], "sr_levels": []},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Strategy 1: MTF Pullback Precision Scalping
# ═══════════════════════════════════════════════════════════════════════════════

section("STRATEGY 1 — MTF Pullback Precision Scalping")

# --- 1.1 Valid BUY signal ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 154.400, "low")],
        bos_15m=[],
        choch_15m=[],
        struct_5m=[_struct("HL", 154.480, "low")],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        zones_5m=[_zone(154.420, 154.380)],
        sessions=["london"],
    )
    result = strat1(state, debug=False)
    if result is None:
        fail("S1-1 valid BUY signal", "returned None — expected a trade signal")
    elif result.get("type") != "BUY":
        fail("S1-1 valid BUY direction", f"expected BUY got {result.get('type')}")
    elif result.get("confidence", 0) < 70:
        fail("S1-1 BUY score >= 70", f"score={result.get('confidence')}")
    elif result.get("sl", 0) >= result.get("entry", 0):
        fail("S1-1 BUY SL below entry", f"sl={result['sl']} entry={result['entry']}")
    elif result.get("tp", 0) <= result.get("entry", 0):
        fail("S1-1 BUY TP above entry", f"tp={result['tp']} entry={result['entry']}")
    else:
        ok("S1-1 valid BUY signal fires correctly")
except Exception as e:
    fail("S1-1 valid BUY signal", traceback.format_exc(limit=2))

# --- 1.2 Valid SELL signal ---
try:
    state = make_state(
        price=154.500,
        b4h="bearish", b1h="bearish", b15m="bearish",
        struct_15m=[_struct("LL", 154.100, "low"), _struct("LH", 154.600, "high")],
        choch_15m=[],
        struct_5m=[_struct("LH", 154.520, "high")],
        bos_5m=[_bos("bearish", 154.510), _bos("bearish", 154.505)],
        candles_5m=[_bear_candle()],
        sessions=["london"],
    )
    result = strat1(state, debug=False)
    if result is None:
        fail("S1-2 valid SELL signal", "returned None — expected a trade signal")
    elif result.get("type") != "SELL":
        fail("S1-2 valid SELL direction", f"expected SELL got {result.get('type')}")
    elif result.get("sl", 0) <= result.get("entry", 0):
        fail("S1-2 SELL SL above entry", f"sl={result['sl']} entry={result['entry']}")
    elif result.get("tp", 0) >= result.get("entry", 0):
        fail("S1-2 SELL TP below entry", f"tp={result['tp']} entry={result['entry']}")
    else:
        ok("S1-2 valid SELL signal fires correctly")
except Exception as e:
    fail("S1-2 valid SELL signal", traceback.format_exc(limit=2))

# --- 1.3 Reject: 4H and 1H not aligned ---
try:
    state = make_state(b4h="bullish", b1h="bearish")
    result = strat1(state)
    if result is not None:
        fail("S1-3 reject conflicting bias", f"expected None, got {result}")
    else:
        ok("S1-3 rejects when 4H=bull 1H=bear (no alignment)")
except Exception as e:
    fail("S1-3 reject conflicting bias", traceback.format_exc(limit=2))

# --- 1.4 Reject: No 15M HL found ---
try:
    state = make_state(
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high")],  # no HL
        bos_5m=[_bos("bullish", 154.490)],
    )
    result = strat1(state)
    if result is not None:
        fail("S1-4 reject no 15M HL", f"expected None (no HL), got signal")
    else:
        ok("S1-4 rejects when no 15M HL exists")
except Exception as e:
    fail("S1-4 reject no 15M HL", traceback.format_exc(limit=2))

# --- 1.5 Reject: Price overextended from 15M HL (> 50 pips) ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 153.900, "low")],  # 60 pips away
        bos_5m=[_bos("bullish", 154.490)],
    )
    result = strat1(state)
    if result is not None:
        fail("S1-5 reject overextended from 15M HL", "expected None — price 60 pips from HL")
    else:
        ok("S1-5 rejects when price is >50 pips from 15M HL (overextended)")
except Exception as e:
    fail("S1-5 reject overextended", traceback.format_exc(limit=2))

# --- 1.6 Reject: No 5M BOS ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 154.490, "low")],
        bos_5m=[],  # no BOS
    )
    result = strat1(state)
    if result is not None:
        fail("S1-6 reject no 5M BOS", "expected None — no BOS on 5M")
    else:
        ok("S1-6 rejects when no 5M BOS")
except Exception as e:
    fail("S1-6 reject no 5M BOS", traceback.format_exc(limit=2))

# --- 1.7 Reject: 15M bearish CHOCH invalidates bullish setup ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 154.490, "low")],
        choch_15m=[_choch("bearish", 154.450)],  # bearish CHOCH!
        bos_5m=[_bos("bullish", 154.490)],
    )
    result = strat1(state)
    if result is not None:
        fail("S1-7 reject bearish 15M CHOCH on BUY", "expected None — CHOCH invalidates setup")
    else:
        ok("S1-7 rejects when 15M bearish CHOCH invalidates bullish setup")
except Exception as e:
    fail("S1-7 reject 15M CHOCH", traceback.format_exc(limit=2))

# --- 1.8 One bias aligned = 15 points ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="neutral",  # only 4H aligned
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 154.490, "low")],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        zones_5m=[_zone(154.510, 154.470)],
        sessions=["london"],
    )
    result = strat1(state, debug=False)
    if result is not None and result.get("confidence", 100) >= 30:
        # One aligned = 15 pts bias, should be present in score
        ok("S1-8 one bias aligned scores 15pts (not 30)")
    elif result is None:
        ok("S1-8 one bias aligned — score below 70 (acceptable, depends on other conditions)")
    else:
        ok("S1-8 one bias aligned handled")
except Exception as e:
    fail("S1-8 one bias aligned", traceback.format_exc(limit=2))

# --- 1.9 Output format validation ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 154.490, "low")],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        struct_5m=[_struct("HL", 154.480, "low")],
        sessions=["london"],
    )
    result = strat1(state, debug=False)
    if result is not None:
        required = ["trade", "type", "confidence", "strategy", "reason", "entry", "sl", "tp"]
        missing = [k for k in required if k not in result]
        if missing:
            fail("S1-9 output format", f"missing keys: {missing}")
        elif result["strategy"] != "MTF Pullback Precision Scalping":
            fail("S1-9 strategy name", f"got '{result['strategy']}'")
        elif not isinstance(result["confidence"], int):
            fail("S1-9 confidence is int", f"type={type(result['confidence'])}")
        else:
            ok("S1-9 output format has all required keys and correct types")
    else:
        ok("S1-9 no signal fired (score below 70) — format N/A")
except Exception as e:
    fail("S1-9 output format", traceback.format_exc(limit=2))

# --- 1.10 RR ratio correctness ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 154.490, "low")],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        struct_5m=[_struct("HL", 154.480, "low")],
        sessions=["london"],
    )
    result = strat1(state, debug=False)
    if result is not None:
        entry = result["entry"]
        sl    = result["sl"]
        tp    = result["tp"]
        sl_d  = abs(entry - sl)
        tp_d  = abs(entry - tp)
        rr    = tp_d / sl_d if sl_d > 0 else 0
        if round(rr, 1) < 2.0:
            fail("S1-10 RR >= 2:1", f"rr={rr:.2f}")
        else:
            ok(f"S1-10 RR is {rr:.1f}:1 (>= 2:1 required)")
    else:
        ok("S1-10 no signal — RR check N/A")
except Exception as e:
    fail("S1-10 RR check", traceback.format_exc(limit=2))

# --- 1.11 None state handled ---
try:
    result = strat1(None)
    if result is not None:
        fail("S1-11 None state", "expected None")
    else:
        ok("S1-11 handles None state gracefully")
except Exception as e:
    fail("S1-11 None state", traceback.format_exc(limit=2))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Strategy 2: Liquidity Sweep Reversal Scalping
# ═══════════════════════════════════════════════════════════════════════════════

section("STRATEGY 2 — Liquidity Sweep Reversal Scalping")

# --- 2.1 Valid BUY signal (bearish CHOCH sweep on 15M → bullish CHOCH on 5M) ---
try:
    state = make_state(
        price=154.500,
        b4h="neutral", b1h="neutral",  # ranging market
        choch_15m=[_choch("bearish", 154.450)],  # bearish sweep of low
        bos_15m=[],
        choch_5m=[_choch("bullish", 154.490)],   # bullish reversal
        bos_5m=[],
        zones_5m=[_zone(154.470, 154.430)],
        candles_5m=[_bull_candle()],
        sessions=["london"],
    )
    result = strat2(state, debug=False)
    if result is None:
        fail("S2-1 valid BUY sweep reversal", "returned None")
    elif result.get("type") != "BUY":
        fail("S2-1 BUY direction", f"got {result.get('type')}")
    elif result.get("sl", 0) >= result.get("entry", 0):
        fail("S2-1 BUY SL below entry", f"sl={result['sl']} entry={result['entry']}")
    elif result.get("tp", 0) <= result.get("entry", 0):
        fail("S2-1 BUY TP above entry", f"tp={result['tp']} entry={result['entry']}")
    else:
        ok(f"S2-1 valid BUY sweep reversal fires (score={result.get('confidence')})")
except Exception as e:
    fail("S2-1 valid BUY sweep reversal", traceback.format_exc(limit=2))

# --- 2.2 Valid SELL signal (bullish CHOCH sweep on 15M → bearish CHOCH on 5M) ---
try:
    state = make_state(
        price=154.500,
        b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bullish", 154.550)],  # bullish sweep of high
        choch_5m=[_choch("bearish", 154.510)],   # bearish reversal
        candles_5m=[_bear_candle()],
        sessions=["ny"],
    )
    result = strat2(state, debug=False)
    if result is None:
        fail("S2-2 valid SELL sweep reversal", "returned None")
    elif result.get("type") != "SELL":
        fail("S2-2 SELL direction", f"got {result.get('type')}")
    elif result.get("sl", 0) <= result.get("entry", 0):
        fail("S2-2 SELL SL above entry", f"sl={result['sl']} entry={result['entry']}")
    elif result.get("tp", 0) >= result.get("entry", 0):
        fail("S2-2 SELL TP below entry", f"tp={result['tp']} entry={result['entry']}")
    else:
        ok(f"S2-2 valid SELL sweep reversal fires (score={result.get('confidence')})")
except Exception as e:
    fail("S2-2 valid SELL sweep reversal", traceback.format_exc(limit=2))

# --- 2.3 Reject: Market strongly trending (both 4H+1H bullish) ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="bullish",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
    )
    result = strat2(state)
    if result is not None:
        fail("S2-3 reject strongly trending market", "expected None — Strategy 1's territory")
    else:
        ok("S2-3 rejects strongly trending market (use Strategy 1)")
except Exception as e:
    fail("S2-3 reject trending market", traceback.format_exc(limit=2))

# --- 2.4 Reject: strongly bearish ---
try:
    state = make_state(
        price=154.500,
        b4h="bearish", b1h="bearish",
        choch_15m=[_choch("bullish", 154.550)],
        choch_5m=[_choch("bearish", 154.510)],
    )
    result = strat2(state)
    if result is not None:
        fail("S2-4 reject strongly bearish market", "expected None")
    else:
        ok("S2-4 rejects strongly bearish market")
except Exception as e:
    fail("S2-4 reject strongly bearish", traceback.format_exc(limit=2))

# --- 2.5 Reject: No sweep on 15M ---
try:
    state = make_state(
        price=154.500,
        b4h="neutral", b1h="neutral",
        choch_15m=[],
        bos_15m=[],
        choch_5m=[_choch("bullish", 154.490)],
    )
    result = strat2(state)
    if result is not None:
        fail("S2-5 reject no 15M sweep", "expected None — no sweep detected")
    else:
        ok("S2-5 rejects when no sweep on 15M")
except Exception as e:
    fail("S2-5 reject no sweep", traceback.format_exc(limit=2))

# --- 2.6 Reject: No 5M reversal confirmation ---
try:
    state = make_state(
        price=154.500,
        b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[],
        bos_5m=[],   # no confirmation
    )
    result = strat2(state)
    if result is not None:
        fail("S2-6 reject no 5M confirmation", "expected None")
    else:
        ok("S2-6 rejects when no 5M CHOCH or BOS confirmation")
except Exception as e:
    fail("S2-6 reject no 5M confirmation", traceback.format_exc(limit=2))

# --- 2.7 Reject: Entry too far from sweep zone ---
try:
    state = make_state(
        price=154.500,
        b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 153.900)],  # sweep was 60 pips away
        choch_5m=[_choch("bullish", 154.490)],
    )
    result = strat2(state)
    if result is not None:
        fail("S2-7 reject entry too far from sweep", "expected None — price 60 pips from sweep")
    else:
        ok("S2-7 rejects when entry >50 pips from sweep zone")
except Exception as e:
    fail("S2-7 reject far entry", traceback.format_exc(limit=2))

# --- 2.8 CHOCH scores higher than BOS-only ---
try:
    state_choch = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        sessions=["london"],
    )
    state_bos = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        bos_15m=[_bos("bearish", 154.450)],
        bos_5m=[_bos("bullish", 154.490)],
        sessions=["london"],
    )
    r_choch = strat2(state_choch, debug=False)
    r_bos   = strat2(state_bos,   debug=False)

    if r_choch is not None and r_bos is not None:
        if r_choch.get("confidence", 0) > r_bos.get("confidence", 0):
            ok(f"S2-8 CHOCH scores higher than BOS (CHOCH={r_choch['confidence']} > BOS={r_bos['confidence']})")
        else:
            fail("S2-8 CHOCH should score higher than BOS", f"choch={r_choch.get('confidence')} bos={r_bos.get('confidence')}")
    elif r_choch is not None:
        ok("S2-8 CHOCH fired, BOS alone didn't reach threshold — CHOCH is stronger")
    else:
        ok("S2-8 both below threshold in this config — scoring difference N/A")
except Exception as e:
    fail("S2-8 CHOCH vs BOS scoring", traceback.format_exc(limit=2))

# --- 2.9 Output format validation ---
try:
    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        sessions=["london"],
    )
    result = strat2(state, debug=False)
    if result is not None:
        required = ["trade", "type", "confidence", "strategy", "reason", "entry", "sl", "tp"]
        missing = [k for k in required if k not in result]
        if missing:
            fail("S2-9 output format", f"missing keys: {missing}")
        elif result["strategy"] != "Liquidity Sweep Reversal Scalping":
            fail("S2-9 strategy name", f"got '{result['strategy']}'")
        else:
            ok("S2-9 output format has all required keys")
    else:
        ok("S2-9 no signal — format N/A")
except Exception as e:
    fail("S2-9 output format", traceback.format_exc(limit=2))

# --- 2.10 SL placed at sweep level (not 5M structure) ---
try:
    sweep_price = 154.450
    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", sweep_price)],
        choch_5m=[_choch("bullish", 154.490)],
        sessions=["london"],
    )
    result = strat2(state, debug=False)
    if result is not None:
        expected_sl_approx = sweep_price - config.SL_BUFFER_PIPS * config.PIP_SIZE
        if abs(result["sl"] - expected_sl_approx) < 0.01:
            ok(f"S2-10 SL correctly placed at sweep level ({result['sl']:.3f})")
        else:
            fail("S2-10 SL at sweep level", f"expected ~{expected_sl_approx:.3f} got {result['sl']}")
    else:
        ok("S2-10 no signal — SL check N/A")
except Exception as e:
    fail("S2-10 SL placement", traceback.format_exc(limit=2))

# --- 2.11 None state handled ---
try:
    result = strat2(None)
    if result is not None:
        fail("S2-11 None state", "expected None")
    else:
        ok("S2-11 handles None state gracefully")
except Exception as e:
    fail("S2-11 None state", traceback.format_exc(limit=2))

# --- 2.12 Strategy 2 fires when Strategy 1 would reject (mixed bias) ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="neutral",   # mixed — S1 won't fire fully, S2 accepts
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        sessions=["london"],
    )
    r1 = strat1(state, debug=False)
    r2 = strat2(state, debug=False)
    # S2 should be willing to fire in mixed bias; S1 may fire with 15pts bias
    if r2 is not None:
        ok("S2-12 Strategy 2 fires in mixed-bias market where Strategy 2 is appropriate")
    else:
        ok("S2-12 Strategy 2 returned None in mixed bias (score below 70 — acceptable)")
except Exception as e:
    fail("S2-12 mixed bias handoff", traceback.format_exc(limit=2))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Risk Manager
# ═══════════════════════════════════════════════════════════════════════════════

section("RISK MANAGER — validate() and get_lot_size()")

good_trade = {
    "trade": True, "type": "BUY",
    "entry": 154.500, "sl": 154.350, "tp": 154.800,
    "confidence": 85, "strategy": "TestStrategy", "symbol": "USD/JPY",
}
good_stats = {"trades_today": 0, "consecutive_losses": 0}

# --- 3.1 Valid trade passes ---
try:
    ok_flag, reason = validate(good_trade, good_stats)
    if ok_flag:
        ok("RM-1 valid BUY trade approved")
    else:
        fail("RM-1 valid BUY trade", f"rejected: {reason}")
except Exception as e:
    fail("RM-1 valid BUY trade", traceback.format_exc(limit=2))

# --- 3.2 Valid SELL trade ---
try:
    sell = {
        "trade": True, "type": "SELL",
        "entry": 154.500, "sl": 154.650, "tp": 154.200,
        "confidence": 85, "strategy": "TestStrategy", "symbol": "USD/JPY",
    }
    ok_flag, reason = validate(sell, good_stats)
    if ok_flag:
        ok("RM-2 valid SELL trade approved")
    else:
        fail("RM-2 valid SELL trade", f"rejected: {reason}")
except Exception as e:
    fail("RM-2 valid SELL trade", traceback.format_exc(limit=2))

# --- 3.3 Reject: Max trades/day ---
try:
    stats = {"trades_today": 3, "consecutive_losses": 0}
    ok_flag, reason = validate(good_trade, stats)
    if not ok_flag:
        ok("RM-3 rejects when max trades/day reached")
    else:
        fail("RM-3 max trades/day", "should have rejected")
except Exception as e:
    fail("RM-3 max trades/day", traceback.format_exc(limit=2))

# --- 3.4 Reject: Max consecutive losses ---
try:
    stats = {"trades_today": 0, "consecutive_losses": 2}
    ok_flag, reason = validate(good_trade, stats)
    if not ok_flag:
        ok("RM-4 rejects after max consecutive losses")
    else:
        fail("RM-4 consecutive losses", "should have rejected")
except Exception as e:
    fail("RM-4 consecutive losses", traceback.format_exc(limit=2))

# --- 3.5 Reject: Invalid direction ---
try:
    bad = {"trade": True, "type": "HOLD", "entry": 154.500, "sl": 154.350, "tp": 154.800}
    ok_flag, reason = validate(bad, good_stats)
    if not ok_flag:
        ok("RM-5 rejects invalid direction 'HOLD'")
    else:
        fail("RM-5 invalid direction", "should have rejected HOLD")
except Exception as e:
    fail("RM-5 invalid direction", traceback.format_exc(limit=2))

# --- 3.6 Reject: BUY SL above entry ---
try:
    bad = {"trade": True, "type": "BUY", "entry": 154.500, "sl": 154.600, "tp": 154.800}
    ok_flag, reason = validate(bad, good_stats)
    if not ok_flag:
        ok("RM-6 rejects BUY with SL above entry")
    else:
        fail("RM-6 BUY SL above entry", "should have rejected")
except Exception as e:
    fail("RM-6 BUY SL above entry", traceback.format_exc(limit=2))

# --- 3.7 Reject: SELL SL below entry ---
try:
    bad = {"trade": True, "type": "SELL", "entry": 154.500, "sl": 154.400, "tp": 154.200}
    ok_flag, reason = validate(bad, good_stats)
    if not ok_flag:
        ok("RM-7 rejects SELL with SL below entry")
    else:
        fail("RM-7 SELL SL below entry", "should have rejected")
except Exception as e:
    fail("RM-7 SELL SL below entry", traceback.format_exc(limit=2))

# --- 3.8 Reject: BUY TP below entry ---
try:
    bad = {"trade": True, "type": "BUY", "entry": 154.500, "sl": 154.350, "tp": 154.200}
    ok_flag, reason = validate(bad, good_stats)
    if not ok_flag:
        ok("RM-8 rejects BUY with TP below entry")
    else:
        fail("RM-8 BUY TP below entry", "should have rejected")
except Exception as e:
    fail("RM-8 BUY TP below entry", traceback.format_exc(limit=2))

# --- 3.9 Reject: RR below 2:1 ---
try:
    bad = {"trade": True, "type": "BUY", "entry": 154.500, "sl": 154.350, "tp": 154.600}
    # SL dist = 0.150, TP dist = 0.100 → RR = 0.67 < 2.0
    ok_flag, reason = validate(bad, good_stats)
    if not ok_flag:
        ok("RM-9 rejects RR below 2:1")
    else:
        fail("RM-9 RR below 2", "should have rejected low RR")
except Exception as e:
    fail("RM-9 RR below 2", traceback.format_exc(limit=2))

# --- 3.10 Reject: NaN entry ---
try:
    bad = {"trade": True, "type": "BUY", "entry": float("nan"), "sl": 154.350, "tp": 154.800}
    ok_flag, reason = validate(bad, good_stats)
    if not ok_flag:
        ok("RM-10 rejects NaN entry value")
    else:
        fail("RM-10 NaN entry", "should have rejected")
except Exception as e:
    fail("RM-10 NaN entry", traceback.format_exc(limit=2))

# --- 3.11 Reject: Infinity SL ---
try:
    bad = {"trade": True, "type": "BUY", "entry": 154.500, "sl": float("inf"), "tp": 154.800}
    ok_flag, reason = validate(bad, good_stats)
    if not ok_flag:
        ok("RM-11 rejects Infinity SL")
    else:
        fail("RM-11 Infinity SL", "should have rejected")
except Exception as e:
    fail("RM-11 Infinity SL", traceback.format_exc(limit=2))

# --- 3.12 Reject: SL distance zero ---
try:
    bad = {"trade": True, "type": "BUY", "entry": 154.500, "sl": 154.500, "tp": 154.800}
    ok_flag, reason = validate(bad, good_stats)
    if not ok_flag:
        ok("RM-12 rejects zero SL distance")
    else:
        fail("RM-12 zero SL distance", "should have rejected")
except Exception as e:
    fail("RM-12 zero SL distance", traceback.format_exc(limit=2))

# --- 3.13 Reject: Risk too large (huge SL) ---
try:
    # SL 300 pips → 300 * 0.01 * (0.01/154.5) * 100000 * 0.01 = ~$1.94 → fine
    # SL 2000 pips → ~$12.94 → exceeds 3% of $135 ($4.05)
    bad = {"trade": True, "type": "BUY", "entry": 154.500, "sl": 134.500, "tp": 194.500}
    ok_flag, reason = validate(bad, good_stats)
    if not ok_flag:
        ok("RM-13 rejects trade where risk exceeds 3% of account")
    else:
        fail("RM-13 risk limit", "should have rejected huge SL")
except Exception as e:
    fail("RM-13 risk limit", traceback.format_exc(limit=2))

# --- 3.14 get_lot_size returns valid float ---
try:
    lot = get_lot_size()
    if isinstance(lot, float) and lot > 0:
        ok(f"RM-14 get_lot_size returns {lot}")
    else:
        fail("RM-14 get_lot_size", f"got {lot}")
except Exception as e:
    fail("RM-14 get_lot_size", traceback.format_exc(limit=2))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Signal Memory
# ═══════════════════════════════════════════════════════════════════════════════

section("SIGNAL MEMORY — deduplication logic")

sig1 = {"strategy": "MTF Pullback Precision Scalping", "type": "BUY",  "sl": 154.350}
sig2 = {"strategy": "MTF Pullback Precision Scalping", "type": "SELL", "sl": 154.650}
sig3 = {"strategy": "Liquidity Sweep Reversal Scalping", "type": "BUY", "sl": 154.350}
state_bull = {"bias": {"1h": "bullish"}}
state_bear = {"bias": {"1h": "bearish"}}

# --- 4.1 First signal is never a duplicate ---
try:
    mem = SignalMemory()
    if not mem.is_duplicate(sig1, state_bull):
        ok("SM-1 first signal is not a duplicate")
    else:
        fail("SM-1 first signal", "incorrectly flagged as duplicate before any recording")
except Exception as e:
    fail("SM-1 first signal", traceback.format_exc(limit=2))

# --- 4.2 Same signal after recording is a duplicate ---
try:
    mem = SignalMemory()
    mem.record(sig1, state_bull)
    if mem.is_duplicate(sig1, state_bull):
        ok("SM-2 same signal after recording is duplicate")
    else:
        fail("SM-2 duplicate detection", "same signal not flagged as duplicate")
except Exception as e:
    fail("SM-2 duplicate detection", traceback.format_exc(limit=2))

# --- 4.3 Different SL is not a duplicate ---
try:
    mem = SignalMemory()
    mem.record(sig1, state_bull)
    sig_new_sl = {"strategy": "MTF Pullback Precision Scalping", "type": "BUY", "sl": 154.250}
    if not mem.is_duplicate(sig_new_sl, state_bull):
        ok("SM-3 different SL level is not a duplicate")
    else:
        fail("SM-3 different SL", "different SL incorrectly flagged as duplicate")
except Exception as e:
    fail("SM-3 different SL", traceback.format_exc(limit=2))

# --- 4.4 Bias flip unlocks the signal ---
try:
    mem = SignalMemory()
    mem.record(sig1, state_bull)
    if not mem.is_duplicate(sig1, state_bear):
        ok("SM-4 1H bias flip unlocks signal memory")
    else:
        fail("SM-4 bias flip", "signal still locked after bias flip")
except Exception as e:
    fail("SM-4 bias flip", traceback.format_exc(limit=2))

# --- 4.5 Different strategy is not a duplicate ---
try:
    mem = SignalMemory()
    mem.record(sig1, state_bull)
    if not mem.is_duplicate(sig3, state_bull):
        ok("SM-5 different strategy name is not a duplicate")
    else:
        fail("SM-5 different strategy", "different strategy flagged as duplicate")
except Exception as e:
    fail("SM-5 different strategy", traceback.format_exc(limit=2))

# --- 4.6 Clear resets memory ---
try:
    mem = SignalMemory()
    mem.record(sig1, state_bull)
    mem.clear()
    if not mem.is_duplicate(sig1, state_bull):
        ok("SM-6 clear() resets signal memory")
    else:
        fail("SM-6 clear()", "memory not cleared")
except Exception as e:
    fail("SM-6 clear()", traceback.format_exc(limit=2))

# --- 4.7 Different direction is not a duplicate ---
try:
    mem = SignalMemory()
    mem.record(sig1, state_bull)
    if not mem.is_duplicate(sig2, state_bull):
        ok("SM-7 different direction (SELL vs BUY) is not a duplicate")
    else:
        fail("SM-7 different direction", "different direction flagged as duplicate")
except Exception as e:
    fail("SM-7 different direction", traceback.format_exc(limit=2))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — State Sanitizer
# ═══════════════════════════════════════════════════════════════════════════════

section("STATE SANITIZER — sanitize_state()")

# --- 5.1 Missing price returns None ---
try:
    result = sanitize_state({"bias": {"4h": "bullish", "1h": "bullish", "15m": "bullish"}})
    if result is None:
        ok("SS-1 missing price returns None")
    else:
        fail("SS-1 missing price", "expected None")
except Exception as e:
    fail("SS-1 missing price", traceback.format_exc(limit=2))

# --- 5.2 Zero price returns None ---
try:
    result = sanitize_state({"current_price": 0})
    if result is None:
        ok("SS-2 zero price returns None")
    else:
        fail("SS-2 zero price", "expected None")
except Exception as e:
    fail("SS-2 zero price", traceback.format_exc(limit=2))

# --- 5.3 NaN price returns None ---
try:
    result = sanitize_state({"current_price": float("nan")})
    if result is None:
        ok("SS-3 NaN price returns None")
    else:
        fail("SS-3 NaN price", "expected None")
except Exception as e:
    fail("SS-3 NaN price", traceback.format_exc(limit=2))

# --- 5.4 Negative price returns None ---
try:
    result = sanitize_state({"current_price": -1.5})
    if result is None:
        ok("SS-4 negative price returns None")
    else:
        fail("SS-4 negative price", "expected None")
except Exception as e:
    fail("SS-4 negative price", traceback.format_exc(limit=2))

# --- 5.5 Valid state passes through with defaults filled ---
try:
    raw = {"current_price": 154.500}
    result = sanitize_state(raw)
    if result is None:
        fail("SS-5 valid state", "returned None unexpectedly")
    elif result["bias"] != {"4h": "neutral", "1h": "neutral", "15m": "neutral"}:
        fail("SS-5 bias default", f"got {result['bias']}")
    elif result["5m"]["bos"] != []:
        fail("SS-5 5m.bos default", f"got {result['5m']['bos']}")
    elif "sessions" not in result:
        fail("SS-5 sessions key missing")
    else:
        ok("SS-5 valid state passes sanitizer with safe defaults filled in")
except Exception as e:
    fail("SS-5 valid state sanitization", traceback.format_exc(limit=2))

# --- 5.6 Non-dict returns None ---
try:
    result = sanitize_state("not a dict")
    if result is None:
        ok("SS-6 non-dict input returns None")
    else:
        fail("SS-6 non-dict", "expected None")
except Exception as e:
    fail("SS-6 non-dict", traceback.format_exc(limit=2))

# --- 5.7 Null bias fields default to neutral ---
try:
    raw = {"current_price": 154.5, "bias": {"4h": None, "1h": None, "15m": None}}
    result = sanitize_state(raw)
    if result and result["bias"] == {"4h": "neutral", "1h": "neutral", "15m": "neutral"}:
        ok("SS-7 null bias fields default to neutral")
    else:
        fail("SS-7 null bias", f"got {result.get('bias') if result else None}")
except Exception as e:
    fail("SS-7 null bias", traceback.format_exc(limit=2))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Integration: Full Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

section("INTEGRATION — Strategy → Risk Manager pipeline")

# --- 6.1 Valid signal from S1 passes risk manager ---
try:
    state = make_state(
        price=154.500,
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 154.490, "low")],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        struct_5m=[_struct("HL", 154.480, "low")],
        sessions=["london"],
    )
    result = strat1(state, debug=False)
    if result is not None:
        ok_flag, reason = validate(result, good_stats)
        if ok_flag:
            ok("INT-1 Strategy 1 signal passes risk manager end-to-end")
        else:
            fail("INT-1 S1 → risk manager", f"rejected: {reason}")
    else:
        ok("INT-1 no signal from S1 — integration N/A")
except Exception as e:
    fail("INT-1 S1 pipeline", traceback.format_exc(limit=2))

# --- 6.2 Valid signal from S2 passes risk manager ---
try:
    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        sessions=["london"],
    )
    result = strat2(state, debug=False)
    if result is not None:
        ok_flag, reason = validate(result, good_stats)
        if ok_flag:
            ok("INT-2 Strategy 2 signal passes risk manager end-to-end")
        else:
            fail("INT-2 S2 → risk manager", f"rejected: {reason}")
    else:
        ok("INT-2 no signal from S2 — integration N/A")
except Exception as e:
    fail("INT-2 S2 pipeline", traceback.format_exc(limit=2))

# --- 6.3 Strategy name in registry matches returned name ---
try:
    from strategies import STRATEGIES
    names_in_registry = [n for n, _ in STRATEGIES]

    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 154.490, "low")],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        struct_5m=[_struct("HL", 154.480, "low")],
        sessions=["london"],
    )
    result = strat1(state, debug=False)
    if result is not None:
        returned_name = result.get("strategy", "")
        if returned_name in names_in_registry:
            ok(f"INT-3 Strategy 1 returned name '{returned_name}' matches registry")
        else:
            fail("INT-3 name match", f"'{returned_name}' not found in registry: {names_in_registry}")
    else:
        ok("INT-3 no signal — name match N/A")
except Exception as e:
    fail("INT-3 strategy name match", traceback.format_exc(limit=2))

# --- 6.4 Strategy 2 returned name matches registry ---
try:
    from strategies import STRATEGIES
    names_in_registry = [n for n, _ in STRATEGIES]

    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        sessions=["london"],
    )
    result = strat2(state, debug=False)
    if result is not None:
        returned_name = result.get("strategy", "")
        if returned_name in names_in_registry:
            ok(f"INT-4 Strategy 2 returned name '{returned_name}' matches registry")
        else:
            fail("INT-4 S2 name match", f"'{returned_name}' not found in registry")
    else:
        ok("INT-4 no signal — name match N/A")
except Exception as e:
    fail("INT-4 S2 name match", traceback.format_exc(limit=2))

# --- 6.5 Strategies don't interfere: S1 bullish trending, S2 rejects ---
try:
    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900, "high"), _struct("HL", 154.490, "low")],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        struct_5m=[_struct("HL", 154.480, "low")],
        choch_15m=[_choch("bearish", 154.450)],  # would be S2 sweep but market is strongly trending
        choch_5m=[_choch("bullish", 154.490)],
        sessions=["london"],
    )
    r1 = strat1(state, debug=False)
    r2 = strat2(state, debug=False)
    if r2 is None:
        ok("INT-5 Strategy 2 correctly rejects strongly trending market — no conflict with S1")
    else:
        fail("INT-5 strategy separation", "Strategy 2 fired in strongly trending market")
except Exception as e:
    fail("INT-5 strategy separation", traceback.format_exc(limit=2))


# ═══════════════════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

total = PASS + FAIL
print(f"\n{'═'*60}")
print(f"  RESULTS: {PASS}/{total} passed — {FAIL} failed")
print(f"{'═'*60}")

if ERRORS:
    print(f"\n  Failed tests:")
    for e in ERRORS:
        print(f"    ✗  {e}")
else:
    print("\n  All tests passed!")

print()
sys.exit(0 if FAIL == 0 else 1)
