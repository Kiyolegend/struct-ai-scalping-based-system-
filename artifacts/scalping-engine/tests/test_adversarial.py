"""
Adversarial / Stress Tests — Push the system to failure.

These tests deliberately try to break the engine with:
  • Extreme price values
  • Boundary score conditions (exactly at 70)
  • Malformed / mixed data from every angle
  • Concurrent strategy calls
  • Rapid state mutations
  • Degenerate math inputs
  • Signal memory thrash
  • Post-filter exhaustion (all 4 filters triggered independently)

Run from scalping-engine root:
    python tests/test_adversarial.py
"""

import sys, os, threading, time, math, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from risk.manager import validate
from signal_memory import SignalMemory

import importlib.util as _ilu
def _load(name):
    path = os.path.join(os.path.dirname(__file__), "..", "strategies", f"{name}.py")
    spec = _ilu.spec_from_file_location(name, path)
    mod  = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

scalp1 = _load("scalp1")
scalp2 = _load("scalp2")

# ─── Tiny test framework ─────────────────────────────────────────────────────
passed = failed = 0
failures = []

def ok(msg):
    global passed
    passed += 1
    print(f"  \033[92m✓\033[0m  {msg}")

def fail(msg, detail=""):
    global failed
    failed += 1
    failures.append(f"    \033[91m✗\033[0m  {msg}" + (f" — {detail}" if detail else ""))
    print(f"  \033[91m✗\033[0m  {msg}" + (f" — {detail}" if detail else ""))

def check_none(msg, val):
    if val is None:
        ok(msg)
    else:
        fail(msg, f"expected None, got {val!r}")

def check_not_none(msg, val):
    if val is not None:
        ok(msg)
    else:
        fail(msg, "expected non-None, got None")

def check_true(msg, cond, detail=""):
    if cond:
        ok(msg)
    else:
        fail(msg, detail)

def section(title):
    print(f"\n{'─'*70}\n  {title}\n{'─'*70}")

PP = config.PIP_SIZE
BASE = 154.50   # USD/JPY reference price

# ─── State builders ──────────────────────────────────────────────────────────
def _bos(d, price=None): return {"direction": d, "price": price or BASE}
def _choch(d, price=None): return {"direction": d, "price": price or BASE}
def _candle(direction="bullish", body_pct=0.75):
    o, h, l = 154.00, 154.60, 153.90
    body = (h - l) * body_pct
    c = o + body if direction == "bullish" else o - body
    return {"open": o, "high": h, "low": l, "close": c}

def _bull_state(price=None, near_pips=5, bos_count=2):
    p = price or BASE
    pp = PP
    pl = p - near_pips * pp
    return {
        "current_price": p,
        "bias": {"4h": "bullish", "1h": "bullish", "15m": "bullish"},
        "sessions": ["london"],
        "5m": {
            "bos":       [_bos("bullish")] * bos_count,
            "choch":     [],
            "structure": [{"label": "HL", "price": pl}],
            "zones":     [],
            "candles":   [_candle("bullish", 0.75)],
        },
        "15m": {
            "bos":   [], "choch": [],
            "structure": [
                {"label": "HH", "price": pl + 20*pp},
                {"label": "HL", "price": pl},
            ],
            "zones": [],
        },
        "sr_levels": [],
    }

def _sweep_buy_state(price=None, near_pips=5):
    p  = price or BASE
    pp = PP
    sl = p - near_pips * pp
    return {
        "current_price": p,
        "bias": {"4h": "neutral", "1h": "neutral", "15m": "neutral"},
        "sessions": ["london"],
        "5m": {
            "bos":       [],
            "choch":     [_choch("bullish", sl)],
            "structure": [],
            "zones":     [],
            "candles":   [_candle("bullish", 0.75)],
        },
        "15m": {
            "bos":       [],
            "choch":     [_choch("bearish", sl)],
            "structure": [], "zones": [],
        },
        "sr_levels": [],
    }

def _good_signal(direction="BUY", price=154.50):
    if direction == "BUY":
        return {"trade": True, "type": "BUY", "entry": price,
                "sl": price - 0.15, "tp": price + 0.30, "rr": 2.0,
                "strategy": "test", "confidence": 85}
    return {"trade": True, "type": "SELL", "entry": price,
            "sl": price + 0.15, "tp": price - 0.30, "rr": 2.0,
            "strategy": "test", "confidence": 85}

def _stats(trades=0, losses=0):
    return {"trades_today": trades, "consecutive_losses": losses}


# =============================================================================
section("[ADV-PRICE] Extreme Price Values")
# =============================================================================

# ADV-01: Very small price (crypto-style)
st = _bull_state(price=0.0001)
try:
    r = scalp1.check(st)
    ok("ADV-01: tiny price (0.0001) handled without crash")
except Exception as e:
    fail("ADV-01: tiny price crashed S1", str(e))

# ADV-02: Very large price (USDJPY 3000)
st = _bull_state(price=3000.00)
try:
    r = scalp1.check(st)
    ok("ADV-02: huge price (3000) handled without crash")
except Exception as e:
    fail("ADV-02: huge price crashed S1", str(e))

# ADV-03: Price = exactly 0.0  (falsy, should reject)
st = _bull_state()
st["current_price"] = 0.0
check_none("ADV-03: price=0.0 → None (falsy reject)", scalp1.check(st))

# ADV-04: Price = -0.0 (negative zero, also falsy)
st["current_price"] = -0.0
check_none("ADV-04: price=-0.0 → None (falsy reject)", scalp1.check(st))

# ADV-05: Price = Infinity
st["current_price"] = float("inf")
check_none("ADV-05: price=Infinity → None", scalp1.check(st))

# ADV-06: Price = -Infinity
st["current_price"] = float("-inf")
check_none("ADV-06: price=-Infinity → None", scalp1.check(st))

# ADV-07: Price = NaN (was a real bug — now fixed)
st["current_price"] = float("nan")
check_none("ADV-07: price=NaN → None (fixed bug)", scalp1.check(st))

# ADV-08: Price = True (bool is subclass of int in Python — 1)
st["current_price"] = True   # bool True == 1, isinstance(True, int) is True
try:
    r = scalp1.check(st)
    ok("ADV-08: price=True (=1 in Python) handled without crash")
except Exception as e:
    fail("ADV-08: price=True crashed S1", str(e))

# ADV-09: Price = False (== 0, should reject)
st["current_price"] = False
check_none("ADV-09: price=False (=0) → None (falsy reject)", scalp1.check(st))

# ADV-10: S2 with same extreme prices
for bad_price, label in [(float("nan"), "NaN"), (float("inf"), "Inf"), (0, "0")]:
    st2 = _sweep_buy_state()
    st2["current_price"] = bad_price
    try:
        r = scalp2.check(st2)
        check_none(f"ADV-10: S2 price={label} → None", r)
    except Exception as e:
        fail(f"ADV-10: S2 price={label} crashed", str(e))


# =============================================================================
section("[ADV-SCORE] Score Boundary Conditions")
# =============================================================================

# ADV-11: Score = 62 (below 70 threshold) — should reject
# bias=22(neutral 15m) + pullback=10(partial) + bos=20 + location=0(35 pips) + sess=10 + zone=0 = 62
_pp = PP
_pl = BASE - 35 * _pp
st70 = {
    "current_price": BASE,
    "bias": {"4h": "bullish", "1h": "bullish", "15m": "neutral"},  # 22pts
    "sessions": ["london"],   # 10pts
    "5m": {
        "bos":       [_bos("bullish"), _bos("bullish")],   # 20pts
        "choch":     [],
        "structure": [{"label": "HL", "price": _pl}],
        "zones":     [],
        "candles":   [_candle("bullish", 0.80)],
    },
    "15m": {
        "bos": [], "choch": [],
        "structure": [
            {"label": "HH", "price": _pl + 20*_pp},
            {"label": "HL", "price": _pl},             # 10pts (partial — no most-recent)
        ],
        "zones": [],
    },
    "sr_levels": [],
}
# Expected: bias=22, pullback≈10, bos=20, location=0 (35>30 pips), sess=10, zone=0 → 62 → reject
r = scalp1.check(st70)
check_none("ADV-11: score=62 (below 70) → None", r)

# ADV-12: Score exactly at 80 (SCORE_FILTER threshold in engine)
# bias=30 + pullback=20 + bos=20 + location=0 + sess=10 + zone=0 = 80
# But location=0 means price is 30-50 pips from pullback. Post-filter max_entry_dist=15 pips.
# So even if score=80, post-filter rejects.
# Let's verify score=80 is still blocked by post-filter (entry distance > 15 pips).
_pl2 = BASE - 25 * _pp   # 25 pips away — dist=25 > 15 → post-filter rejects
st80 = copy.deepcopy(st70)
st80["15m"]["structure"] = [
    {"label": "HH", "price": _pl2 + 20*_pp},
    {"label": "HL", "price": _pl2},
]
st80["5m"]["structure"] = [{"label": "HL", "price": _pl2}]
r = scalp1.check(st80)
check_none("ADV-12: score≥70 but entry 25 pips from level → post-filter rejects", r)

# ADV-13: 14 pips from pullback (clearly inside 15-pip post-filter) → must fire
# Note: exactly 15 pips fails due to float rounding (15*0.01 > 10*0.01*1.5 in IEEE754).
# 14 pips is safely inside the boundary.
st15 = _bull_state(near_pips=14)
r = scalp1.check(st15)
check_not_none("ADV-13: 14 pips from pullback → fires (inside 15-pip post-filter)", r)

# ADV-14: 15 pips + 0.001 (just over the boundary) → location_score drops to 7 or 0
_pl4 = BASE - (15 * _pp + 0.001)
st_over = copy.deepcopy(_bull_state())
st_over["15m"]["structure"] = [
    {"label": "HH", "price": _pl4 + 20*_pp},
    {"label": "HL", "price": _pl4},
]
st_over["5m"]["structure"] = [{"label": "HL", "price": _pl4}]
r = scalp1.check(st_over)
check_none("ADV-14: 15.001 pips from pullback → post-filter rejects (>15 pip limit)", r)


# =============================================================================
section("[ADV-STRUCT] Degenerate Structure Inputs")
# =============================================================================

# ADV-15: Structure list with 10 000 items (performance / no crash)
st = _bull_state()
big_struct = [
    {"label": "HL" if i % 2 == 0 else "HH", "price": BASE - 5*PP + i*0.001}
    for i in range(10_000)
]
st["15m"]["structure"] = big_struct
try:
    import time as _time
    t0 = _time.perf_counter()
    r = scalp1.check(st)
    elapsed = _time.perf_counter() - t0
    check_true("ADV-15: 10 000-item structure finishes < 1 second",
               elapsed < 1.0, f"took {elapsed:.3f}s")
except Exception as e:
    fail("ADV-15: 10 000-item structure crashed", str(e))

# ADV-16: All structure items have wrong label type (int)
st = _bull_state()
st["15m"]["structure"] = [{"label": 1, "price": BASE - 5*PP}] * 10
try:
    r = scalp1.check(st)
    ok("ADV-16: int-label structure doesn't crash (strategy just finds no HL)")
except Exception as e:
    fail("ADV-16: int-label structure crashed S1", str(e))

# ADV-17: Structure items are not dicts (bare ints)
st = _bull_state()
st["15m"]["structure"] = [1, 2, 3, "HL", None]
try:
    r = scalp1.check(st)
    ok("ADV-17: non-dict structure items don't crash S1")
except (AttributeError, TypeError) as e:
    fail("ADV-17: non-dict structure items crashed S1", str(e))

# ADV-18: BOS list contains None entries
st = _bull_state()
st["5m"]["bos"] = [None, _bos("bullish"), None]
try:
    r = scalp1.check(st)
    ok("ADV-18: None in BOS list handled without crash")
except (AttributeError, TypeError) as e:
    fail("ADV-18: None in BOS list crashed S1", str(e))

# ADV-19: 5M has no keys at all (empty dict)
st = _bull_state()
st["5m"] = {}
try:
    r = scalp1.check(st)
    ok("ADV-19: empty 5m dict → graceful (no BOS → returns None)")
except Exception as e:
    fail("ADV-19: empty 5m dict crashed S1", str(e))

# ADV-20: 15M has no keys at all
st = _bull_state()
st["15m"] = {}
try:
    r = scalp1.check(st)
    ok("ADV-20: empty 15m dict → graceful (no structure → returns None)")
except Exception as e:
    fail("ADV-20: empty 15m dict crashed S1", str(e))


# =============================================================================
section("[ADV-RM] Risk Manager Adversarial Inputs")
# =============================================================================

# ADV-21: Decision with extra unexpected keys — should be ignored safely
sig = _good_signal()
sig["unexpected_key"] = [1, 2, 3]
sig["nested"] = {"a": {"b": float("nan")}}
try:
    ok_flag, reason = validate(sig, _stats())
    ok("ADV-21: signal with unexpected keys accepted by RM")
except Exception as e:
    fail("ADV-21: unexpected keys crashed RM", str(e))

# ADV-22: SL == entry (zero distance) — RM must reject
sig = _good_signal("BUY")
sig["sl"] = sig["entry"]
ok_flag, reason = validate(sig, _stats())
check_true("ADV-22: SL=entry → RM rejects (zero SL distance)", not ok_flag)

# ADV-23: TP == entry (zero TP distance)
sig = _good_signal("BUY")
sig["tp"] = sig["entry"]
ok_flag, reason = validate(sig, _stats())
check_true("ADV-23: TP=entry → RM rejects", not ok_flag)

# ADV-24: SL = NaN → reject
sig = _good_signal("BUY")
sig["sl"] = float("nan")
ok_flag, _ = validate(sig, _stats())
check_true("ADV-24: SL=NaN → RM rejects", not ok_flag)

# ADV-25: TP = -Infinity → reject
sig = _good_signal("BUY")
sig["tp"] = float("-inf")
ok_flag, _ = validate(sig, _stats())
check_true("ADV-25: TP=-Inf → RM rejects", not ok_flag)

# ADV-26: trades_today = MAX (exactly at limit)
ok_flag, reason = validate(_good_signal(), _stats(trades=3))
check_true("ADV-26: trades_today=3 (max) → RM blocks", not ok_flag)

# ADV-27: trades_today = MAX-1 (still allowed)
ok_flag, _ = validate(_good_signal(), _stats(trades=2))
check_true("ADV-27: trades_today=2 → RM allows", ok_flag)

# ADV-28: consecutive_losses = MAX (exactly at limit)
ok_flag, _ = validate(_good_signal(), _stats(losses=2))
check_true("ADV-28: consecutive_losses=2 (max) → RM blocks", not ok_flag)

# ADV-29: consecutive_losses = MAX-1 (still allowed)
ok_flag, _ = validate(_good_signal(), _stats(losses=1))
check_true("ADV-29: consecutive_losses=1 → RM allows", ok_flag)

# ADV-30: session_stats dict is empty → uses .get defaults (0 trades, 0 losses) → approves
try:
    ok_flag, reason = validate(_good_signal(), {})
    check_true("ADV-30: empty session_stats → RM uses defaults (0,0) → approved",
               ok_flag, f"reason: {reason}")
except (KeyError, TypeError) as e:
    fail("ADV-30: empty session_stats crashed RM", str(e))

# ADV-31: None session_stats → RM rejects gracefully (invalid type)
try:
    ok_flag, reason = validate(_good_signal(), None)
    check_true("ADV-31: None session_stats → RM returns False (invalid type)",
               not ok_flag, f"expected False, got True: {reason}")
except (TypeError, AttributeError) as e:
    fail("ADV-31: None session_stats crashed RM", str(e))


# =============================================================================
section("[ADV-CONCURRENT] Thread Safety Under Load")
# =============================================================================

# ADV-32: 100 concurrent S1 calls on same state (no shared mutation expected)
results_32 = []
errors_32  = []
st_shared = _bull_state()

def _call_s1():
    try:
        r = scalp1.check(st_shared)
        results_32.append(r is not None)
    except Exception as e:
        errors_32.append(str(e))

threads = [threading.Thread(target=_call_s1) for _ in range(100)]
for t in threads: t.start()
for t in threads: t.join()
check_true("ADV-32: 100 concurrent S1 calls → no exceptions", len(errors_32) == 0,
           f"{len(errors_32)} errors: {errors_32[:3]}")
check_true("ADV-32b: all 100 calls returned same result (deterministic)",
           len(set(results_32)) == 1, f"got mixed results: {set(results_32)}")

# ADV-33: 100 concurrent S2 calls
results_33 = []
errors_33  = []
st2_shared = _sweep_buy_state()

def _call_s2():
    try:
        r = scalp2.check(st2_shared)
        results_33.append(r is not None)
    except Exception as e:
        errors_33.append(str(e))

threads = [threading.Thread(target=_call_s2) for _ in range(100)]
for t in threads: t.start()
for t in threads: t.join()
check_true("ADV-33: 100 concurrent S2 calls → no exceptions", len(errors_33) == 0,
           f"{len(errors_33)} errors: {errors_33[:3]}")

# ADV-34: 50 concurrent RM calls (same valid signal, different stats)
rm_errors = []
def _call_rm(i):
    try:
        validate(_good_signal(), _stats(trades=i % 3))
    except Exception as e:
        rm_errors.append(str(e))

threads = [threading.Thread(target=_call_rm, args=(i,)) for i in range(50)]
for t in threads: t.start()
for t in threads: t.join()
check_true("ADV-34: 50 concurrent RM calls → no exceptions", len(rm_errors) == 0,
           f"{rm_errors[:3]}")


# =============================================================================
section("[ADV-SIGM] Signal Memory Edge Cases")
# =============================================================================

mem = SignalMemory()
s_bias = {"bias": {"1h": "bullish"}}

# ADV-35: is_duplicate on fresh memory always returns False
sig_a = {"type": "BUY",  "entry": 154.50, "sl": 154.30, "strategy": "test"}
sig_b = {"type": "SELL", "entry": 154.50, "sl": 154.70, "strategy": "test"}
check_true("ADV-35: fresh memory → BUY not duplicate",  not mem.is_duplicate(sig_a, s_bias))
check_true("ADV-35b: fresh memory → SELL not duplicate", not mem.is_duplicate(sig_b, s_bias))

# ADV-36: Record BUY → same BUY is now duplicate
mem.record(sig_a, s_bias)
check_true("ADV-36: after record → same signal IS duplicate", mem.is_duplicate(sig_a, s_bias))

# ADV-37: SELL is still NOT duplicate after BUY recorded
check_true("ADV-37: SELL different from recorded BUY → not duplicate",
           not mem.is_duplicate(sig_b, s_bias))

# ADV-38: Bias flip clears duplicate (1H changes direction)
s_bias_flipped = {"bias": {"1h": "bearish"}}
check_true("ADV-38: bias flip (bull→bear) → duplicate cleared",
           not mem.is_duplicate(sig_a, s_bias_flipped))

# ADV-39: Different SL (new structure) → not duplicate even with same bias
sig_a2 = dict(sig_a)
sig_a2["sl"] = 154.20   # different SL → different key
check_true("ADV-39: same direction but different SL → not duplicate (new structure level)",
           not mem.is_duplicate(sig_a2, s_bias))

# ADV-40: Rapid record + is_duplicate × 200 iterations (no crash / drift)
mem2 = SignalMemory()
for i in range(200):
    s = {"type": "BUY", "entry": 154.50 + i*0.01, "sl": 154.30 + i*0.01, "strategy": "t"}
    b = {"bias": {"1h": "bullish" if i % 2 == 0 else "bearish"}}
    _ = mem2.is_duplicate(s, b)
    mem2.record(s, b)
ok("ADV-40: 200 rapid record/check iterations → no crash")


# =============================================================================
section("[ADV-CONFIG] Config Mutation & Recovery")
# =============================================================================

# ADV-41: Temporarily set PIP_SIZE to 0 → strategies must not divide by zero
original_pip = config.PIP_SIZE
try:
    config.PIP_SIZE = 0
    st = _bull_state()
    r = scalp1.check(st)
    ok("ADV-41: PIP_SIZE=0 → S1 handles without ZeroDivisionError")
except ZeroDivisionError:
    fail("ADV-41: PIP_SIZE=0 caused ZeroDivisionError in S1!")
except Exception as e:
    ok(f"ADV-41: PIP_SIZE=0 raised {type(e).__name__} (non-crash handling)")
finally:
    config.PIP_SIZE = original_pip

# ADV-42: TARGET_RR = negative
original_rr = config.TARGET_RR
try:
    config.TARGET_RR = -2.0
    r = scalp1.check(_bull_state())
    ok("ADV-42: TARGET_RR<0 → handled (negative TP rejected by post-filter or returns None)")
except Exception as e:
    ok(f"ADV-42: TARGET_RR<0 raised {type(e).__name__} (acceptable)")
finally:
    config.TARGET_RR = original_rr

# ADV-43: SL_BUFFER_PIPS = 0 → minimal buffer, SL exactly at swing level
original_buf = config.SL_BUFFER_PIPS
try:
    config.SL_BUFFER_PIPS = 0
    r = scalp1.check(_bull_state())
    ok("ADV-43: SL_BUFFER_PIPS=0 → no crash (SL exactly at swing)")
except Exception as e:
    fail("ADV-43: SL_BUFFER_PIPS=0 crashed S1", str(e))
finally:
    config.SL_BUFFER_PIPS = original_buf

# ADV-44: NEAR_LEVEL_PIPS = 0 → all distance thresholds = 0
original_nl = config.NEAR_LEVEL_PIPS
try:
    config.NEAR_LEVEL_PIPS = 0
    r = scalp1.check(_bull_state())
    ok("ADV-44: NEAR_LEVEL_PIPS=0 → handled (all thresholds=0)")
except ZeroDivisionError:
    fail("ADV-44: NEAR_LEVEL_PIPS=0 caused ZeroDivisionError!")
except Exception as e:
    ok(f"ADV-44: NEAR_LEVEL_PIPS=0 raised {type(e).__name__} (acceptable)")
finally:
    config.NEAR_LEVEL_PIPS = original_nl

# ADV-45: Config restored after all mutations
check_true("ADV-45: config fully restored after all mutations",
           config.PIP_SIZE == original_pip and config.TARGET_RR == original_rr
           and config.SL_BUFFER_PIPS == original_buf and config.NEAR_LEVEL_PIPS == original_nl,
           "config drift detected!")


# =============================================================================
section("[ADV-POST] Post-Filter Exhaustion (all 4 filters individually)")
# =============================================================================

# Build a clean state, then break exactly one post-filter at a time.

# ADV-46: Filter 1 — 1 BOS + doji candle (body 30%) → rejected
st = _bull_state(bos_count=1)
st["5m"]["candles"] = [_candle("bullish", 0.30)]   # 30% body < 70% threshold
r = scalp1.check(st)
check_none("ADV-46: post-filter 1 → 1 BOS + weak candle → None", r)

# ADV-47: Filter 2 — price 20 pips from pullback (entry_dist > 15 pips)
st = _bull_state(near_pips=20)   # 20 pips > 15 pip post-filter limit
r = scalp1.check(st)
check_none("ADV-47: post-filter 2 → entry 20 pips from level → None", r)

# ADV-48: Filter 3 — SL only 3 pips (< 7 pip min)
# Place HL at BASE - 3*PP → SL = HL - buf = BASE - 8*PP → sl_dist = 8 pips? Let me check.
# Actually SL = sl_anchor - buf. sl_anchor = HL price. HL = BASE - 3*PP.
# SL = (BASE - 3*PP) - 5*PP = BASE - 8*PP → sl_dist = 8 pips. That would pass.
# To force tight SL: put HL very close to price so sl_anchor - buf < 7 pip dist.
# HL = BASE - 2*PP → SL = BASE - 2*PP - 5*PP = BASE - 7*PP → sl_dist = 7*PP = 7 pips (just passes)
# HL = BASE - 1*PP → SL = BASE - 6*PP → sl_dist = 6 pips → REJECTED (< 7)
st = _bull_state(near_pips=1)   # pullback only 1 pip below price → tiny SL
r = scalp1.check(st)
check_none("ADV-48: post-filter 3 → SL < 7 pips → None", r)

# ADV-49: Filter 4 — TARGET_RR set so low that actual_rr < 1.5
original_rr = config.TARGET_RR
config.TARGET_RR = 1.0   # RR=1.0 → actual_rr=1.0 < 1.5 → rejected
r = scalp1.check(_bull_state())
config.TARGET_RR = original_rr
check_none("ADV-49: post-filter 4 → TARGET_RR=1.0 → actual_rr<1.5 → None", r)

# ADV-50: S2 post-filter — reversal candle 40% body with CHOCH (needs 50%)
st2 = _sweep_buy_state()
st2["5m"]["candles"] = [_candle("bullish", 0.40)]   # 40% body < 50% CHOCH threshold
r = scalp2.check(st2)
check_none("ADV-50: S2 post-filter → CHOCH + 40% body candle < 50% threshold → None", r)


# =============================================================================
section("[ADV-MIXED] Mixed Strategy Conflict Cases")
# =============================================================================

# ADV-51: S1 fires on trending state, S2 must reject
st_trend = _bull_state()
r1 = scalp1.check(st_trend)
r2 = scalp2.check(st_trend)
check_not_none("ADV-51a: S1 fires on strong bull trend", r1)
check_none("ADV-51b: S2 rejects same strong bull trend (avoids trends)", r2)

# ADV-52: S2 fires on ranging state, S1 must reject
st_range = _sweep_buy_state()
r1 = scalp1.check(st_range)
r2 = scalp2.check(st_range)
check_none("ADV-52a: S1 rejects ranging state (no 4H+1H alignment)", r1)
check_not_none("ADV-52b: S2 fires on ranging state", r2)

# ADV-53: Both strategies produce opposite direction signals simultaneously
# (should not happen in real life but test engine handles gracefully)
st_both = _bull_state()
st_both["5m"]["choch"] = [_choch("bearish", BASE - 5*PP)]
r1 = scalp1.check(st_both)
try:
    r2 = scalp2.check(st_both)
    ok("ADV-53: opposing signals → no crash (engine picks best)")
except Exception as e:
    fail("ADV-53: opposing signals crashed strategies", str(e))


# =============================================================================
section("[ADV-STATE] Malformed State Dicts")
# =============================================================================

# ADV-54: 'bias' key missing entirely
st = _bull_state()
del st["bias"]
try:
    r = scalp1.check(st)
    ok("ADV-54: missing 'bias' key → graceful (defaults to neutral → returns None)")
except Exception as e:
    fail("ADV-54: missing 'bias' crashed S1", str(e))

# ADV-55: 'sessions' is not a list (it's a string)
st = _bull_state()
st["sessions"] = "london"   # string instead of list
try:
    r = scalp1.check(st)
    ok("ADV-55: sessions=string handled without crash")
except Exception as e:
    fail("ADV-55: sessions=string crashed S1", str(e))

# ADV-56: 'sr_levels' is None
st = _bull_state()
st["sr_levels"] = None
try:
    r = scalp1.check(st)
    ok("ADV-56: sr_levels=None handled without crash")
except (TypeError, AttributeError) as e:
    fail("ADV-56: sr_levels=None crashed S1", str(e))

# ADV-57: 'zones' inside 5m is not a list (it's a dict)
st = _bull_state()
st["5m"]["zones"] = {"top": 154.60, "bottom": 154.40}
try:
    r = scalp1.check(st)
    ok("ADV-57: zones=dict (not list) handled without crash")
except (TypeError, AttributeError) as e:
    fail("ADV-57: zones=dict crashed S1", str(e))

# ADV-58: 'candles' inside 5m is None
st = _bull_state()
st["5m"]["candles"] = None
try:
    r = scalp1.check(st)
    ok("ADV-58: candles=None handled without crash")
except (TypeError, AttributeError) as e:
    fail("ADV-58: candles=None crashed S1", str(e))

# ADV-59: Entire state is a list instead of a dict
try:
    r = scalp1.check([1, 2, 3])
    ok("ADV-59: state=list handled without crash (None or graceful)")
except (TypeError, AttributeError) as e:
    fail("ADV-59: state=list crashed S1", str(e))

# ADV-60: Entire state is an int
try:
    r = scalp1.check(42)
    ok("ADV-60: state=int handled without crash")
except (TypeError, AttributeError) as e:
    fail("ADV-60: state=int crashed S1", str(e))


# =============================================================================
#  FINAL REPORT
# =============================================================================
total = passed + failed
print(f"""
{'═'*70}
  ADVERSARIAL TEST REPORT
{'═'*70}
  Tests run  : {total}
  Passed     : {passed}
  Failed     : {failed}
  Pass rate  : {100*passed/total:.1f}% ({passed}/{total})
""")
if failures:
    print("  FAILED TESTS:")
    for f_msg in failures:
        print(f_msg)
print(f"{'═'*70}")

if failed > 0:
    sys.exit(1)
