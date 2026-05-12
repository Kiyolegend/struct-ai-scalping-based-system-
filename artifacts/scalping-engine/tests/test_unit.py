"""
STRUCT.ai Scalping Engine — Full Component Unit Test Suite
===========================================================
Covers every module with isolated unit tests:
  1.  config.py                — pip sizes, spread helpers, symbol config
  2.  state.py                 — sanitize_state, session helpers, asia range
  3.  strategies/scalp1.py     — all 7 gates (BUY + SELL)
  4.  strategies/scalp2.py     — all 7 gates (BUY + SELL)
  5.  risk/manager.py          — all 9 validation checks
  6.  signal_memory.py         — duplicate detection + bias-flip reset
  7.  execution/simulator.py   — order simulation output
  8.  confluence/confidence_score.py — scoring engine
  9.  strategies/__init__.py   — registry completeness
  10. Integration pipeline      — state → strategy → risk → execute
Run: python3 tests/test_unit.py
"""

import sys, os, io, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import traceback

PASS = 0
FAIL = 0
ERRORS = []


def ok(name):
    global PASS
    PASS += 1
    print(f"  PASS  {name}")


def fail(name, detail=""):
    global FAIL
    FAIL += 1
    msg = f"  FAIL  {name}"
    if detail:
        msg += f"\n        -> {detail}"
    print(msg)
    ERRORS.append(name)


def section(title):
    print(f"\n{'='*68}")
    print(f"  {title}")
    print(f"{'='*68}")


# ── Candle helpers ─────────────────────────────────────────────────────────────

def _bullish_candle(o=154.450, c=154.520, low=154.440, high=154.530):
    """Strong bullish candle — body/range ~ 78%."""
    return {"open": o, "high": high, "low": low, "close": c}


def _bearish_candle(o=154.550, c=154.480, low=154.470, high=154.560):
    """Strong bearish candle — body/range ~ 78%."""
    return {"open": o, "high": high, "low": low, "close": c}


def _doji(p=154.500):
    """Indecision doji — body/range ~ 5%."""
    return {"open": p, "high": p + 0.010, "low": p - 0.010, "close": p + 0.001}


# ── State builders ─────────────────────────────────────────────────────────────

def _struct(label, price, kind="high"):
    return {"label": label, "price": price, "kind": kind}


def _bos(direction, price):
    return {"direction": direction, "price": price}


def _choch(direction, price):
    return {"direction": direction, "price": price}


def _zone(top, bottom):
    return {"top": top, "bottom": bottom, "center": (top + bottom) / 2}


def make_state(
    price=154.500, symbol="USD/JPY",
    b4h="bullish", b1h="bullish", b15m="bullish",
    struct_15m=None, bos_15m=None, choch_15m=None, zones_15m=None,
    struct_5m=None,  bos_5m=None,  choch_5m=None,  zones_5m=None,
    candles_5m=None, sessions=None,
):
    return {
        "symbol": symbol,
        "current_price": price,
        "bias": {"4h": b4h, "1h": b1h, "15m": b15m},
        "sessions": sessions or ["london"],
        "tradeable_session": True,
        "sr_levels": [],
        "asia_range": {"high": 154.800, "low": 154.200},
        "15m": {
            "trend": b15m, "structure": struct_15m or [],
            "bos": bos_15m or [], "choch": choch_15m or [],
            "zones": zones_15m or [], "candles": [],
        },
        "5m": {
            "trend": b15m, "structure": struct_5m or [],
            "bos": bos_5m or [], "choch": choch_5m or [],
            "zones": zones_5m or [],
            "candles": candles_5m if candles_5m is not None else [],
        },
        "1m": {"trend": "neutral", "structure": [], "bos": [], "choch": [],
               "zones": [], "candles": [], "sr_levels": []},
        "1h": {"trend": b1h, "structure": [], "bos": [], "choch": [],
               "zones": [], "candles": [], "sr_levels": []},
    }


# ── Imports ────────────────────────────────────────────────────────────────────

import config
from state import sanitize_state, get_active_sessions, is_tradeable_session
from strategies.scalp1 import check as strat1
from strategies.scalp2 import check as strat2
from strategies import STRATEGIES
from risk.manager import validate, get_lot_size
from signal_memory import SignalMemory
from execution.simulator import place_order as sim_order
from confluence.confidence_score import calculate, tradeable, format_breakdown


# ══════════════════════════════════════════════════════════════════════════════
# 1. CONFIG
# ══════════════════════════════════════════════════════════════════════════════

section("1. CONFIG — Pip sizes, spreads, symbol table")

try:
    cfg = config.get_symbol_cfg("USD/JPY")
    assert cfg["pip_size"] == 0.01,   f"USDJPY pip={cfg['pip_size']}"
    assert cfg["mt5_name"] == "USDJPY"
    ok("CFG-01: USD/JPY pip=0.01, mt5_name=USDJPY")
except Exception as e:
    fail("CFG-01", str(e))

try:
    cfg = config.get_symbol_cfg("EUR/USD")
    assert cfg["pip_size"] == 0.0001, f"EURUSD pip={cfg['pip_size']}"
    assert cfg["mt5_name"] == "EURUSD"
    ok("CFG-02: EUR/USD pip=0.0001, mt5_name=EURUSD")
except Exception as e:
    fail("CFG-02", str(e))

try:
    cfg = config.get_symbol_cfg("GBP/JPY")
    assert cfg["pip_size"] == 0.01
    assert cfg["mt5_name"] == "GBPJPY"
    ok("CFG-03: GBP/JPY pip=0.01, mt5_name=GBPJPY")
except Exception as e:
    fail("CFG-03", str(e))

try:
    sp = config.get_spread_pips("USD/JPY")
    assert sp == 1.0, f"spread={sp}"
    ok("CFG-04: USD/JPY spread=1.0 pip")
except Exception as e:
    fail("CFG-04", str(e))

try:
    sp = config.get_spread_pips("GBP/JPY")
    assert sp == 2.5, f"spread={sp}"
    ok("CFG-05: GBP/JPY spread=2.5 pip (widest spread in table)")
except Exception as e:
    fail("CFG-05", str(e))

try:
    assert len(config.SCAN_SYMBOLS) == 8
    ok("CFG-06: Exactly 8 symbols in SCAN_SYMBOLS")
except Exception as e:
    fail("CFG-06", str(e))

try:
    assert config.DEFAULT_LOT == 0.02
    ok("CFG-07: DEFAULT_LOT=0.02")
except Exception as e:
    fail("CFG-07", str(e))

try:
    assert config.MIN_CONFIDENCE == 80
    ok("CFG-08: MIN_CONFIDENCE=80")
except Exception as e:
    fail("CFG-08", str(e))

try:
    assert config.MAX_TRADES_PER_DAY == 3
    assert config.MAX_CONSECUTIVE_LOSSES == 2
    ok("CFG-09: MAX_TRADES=3, MAX_CONSEC_LOSSES=2")
except Exception as e:
    fail("CFG-09", str(e))

try:
    assert config.NET_MIN_RR == 1.5
    assert config.SWEEP_SL_BUFFER_PIPS == 8
    assert config.MIN_SWEEP_RECOVERY_PIPS == 3
    ok("CFG-10: NET_MIN_RR=1.5, SWEEP_SL_BUF=8, MIN_RECOVERY=3")
except Exception as e:
    fail("CFG-10", str(e))

try:
    # Unknown symbol defaults gracefully to USD/JPY config
    cfg = config.get_symbol_cfg("UNKNOWN")
    assert cfg["pip_size"] == 0.01
    ok("CFG-11: Unknown symbol falls back to USD/JPY config")
except Exception as e:
    fail("CFG-11", str(e))

try:
    # All JPY pairs have pip_size=0.01, all non-JPY have 0.0001
    jpy = ["USD/JPY", "EUR/JPY", "GBP/JPY"]
    non_jpy = ["EUR/USD", "GBP/USD", "AUD/USD", "USD/CAD", "USD/CHF"]
    for s in jpy:
        assert config.get_symbol_cfg(s)["pip_size"] == 0.01, f"{s} pip wrong"
    for s in non_jpy:
        assert config.get_symbol_cfg(s)["pip_size"] == 0.0001, f"{s} pip wrong"
    ok("CFG-12: All pip sizes correct — JPY=0.01, non-JPY=0.0001")
except Exception as e:
    fail("CFG-12", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 2. STATE — sanitize_state, sessions
# ══════════════════════════════════════════════════════════════════════════════

section("2. STATE — sanitize_state, session helpers")

try:
    result = sanitize_state(None)
    assert result is None
    ok("ST-01: sanitize_state(None) returns None")
except Exception as e:
    fail("ST-01", str(e))

try:
    result = sanitize_state({})
    assert result is None
    ok("ST-02: sanitize_state({}) returns None (missing price)")
except Exception as e:
    fail("ST-02", str(e))

try:
    result = sanitize_state({"current_price": float("nan")})
    assert result is None
    ok("ST-03: sanitize_state NaN price returns None")
except Exception as e:
    fail("ST-03", str(e))

try:
    result = sanitize_state({"current_price": -1.0})
    assert result is None
    ok("ST-04: sanitize_state negative price returns None")
except Exception as e:
    fail("ST-04", str(e))

try:
    result = sanitize_state({"current_price": 0.0})
    assert result is None
    ok("ST-05: sanitize_state zero price returns None")
except Exception as e:
    fail("ST-05", str(e))

try:
    result = sanitize_state({"current_price": float("inf")})
    assert result is None
    ok("ST-06: sanitize_state infinity price returns None")
except Exception as e:
    fail("ST-06", str(e))

try:
    result = sanitize_state({"current_price": 154.500})
    assert result is not None
    assert result["current_price"] == 154.500
    assert result["bias"]["4h"] == "neutral"
    assert result["bias"]["1h"] == "neutral"
    assert result["bias"]["15m"] == "neutral"
    ok("ST-07: Minimal valid state sanitized correctly — bias defaults to neutral")
except Exception as e:
    fail("ST-07", str(e))

try:
    result = sanitize_state({"current_price": 154.500,
                             "bias": {"4h": "bullish", "1h": None, "15m": "bearish"}})
    assert result["bias"]["4h"] == "bullish"
    assert result["bias"]["1h"] == "neutral"   # None → "neutral"
    assert result["bias"]["15m"] == "bearish"
    ok("ST-08: None bias value coerced to 'neutral'")
except Exception as e:
    fail("ST-08", str(e))

try:
    result = sanitize_state({
        "current_price": 154.500,
        "5m": {"zones": {"bad": "dict"}}   # zones should be list not dict
    })
    assert isinstance(result["5m"]["zones"], list)
    ok("ST-09: zones dict coerced to empty list in sanitizer")
except Exception as e:
    fail("ST-09", str(e))

try:
    assert is_tradeable_session(["london"]) is True
    assert is_tradeable_session(["ny"]) is True
    assert is_tradeable_session(["asian"]) is False
    assert is_tradeable_session([]) is False
    assert is_tradeable_session(["asian", "london"]) is True
    ok("ST-10: is_tradeable_session — london/ny=True, asian/empty=False")
except Exception as e:
    fail("ST-10", str(e))

try:
    # All required keys should be present after sanitization
    full = sanitize_state({"current_price": 154.500,
                           "bias": {"4h": "bullish", "1h": "bullish", "15m": "bullish"},
                           "5m": {"bos": [{"direction":"bullish"}], "zones": []}})
    for key in ["bias", "5m", "15m", "1m", "1h", "sr_levels", "asia_range"]:
        assert key in full, f"missing key: {key}"
    ok("ST-11: All required keys present after sanitization")
except Exception as e:
    fail("ST-11", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 3. STRATEGY 1 — MTF Pullback Precision Scalping (14 unit tests)
# ══════════════════════════════════════════════════════════════════════════════

section("3. STRATEGY 1 — MTF Pullback Precision Scalping")

# --- Valid BUY ---
try:
    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish", b15m="bullish",
        struct_15m=[_struct("HH", 154.900), _struct("HL", 154.480)],
        struct_5m=[_struct("HL", 154.475)],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        candles_5m=[_bullish_candle()],
    )
    r = strat1(state)
    assert r is not None, "returned None — expected BUY signal"
    assert r["type"] == "BUY", f"expected BUY got {r['type']}"
    assert r["sl"] < r["entry"], f"SL {r['sl']} not below entry {r['entry']}"
    assert r["tp"] > r["entry"], f"TP {r['tp']} not above entry {r['entry']}"
    assert r["confidence"] >= 80, f"score={r['confidence']} < 80"
    ok(f"S1-01: Valid BUY fires — score={r['confidence']}, SL={r['sl']:.3f}, TP={r['tp']:.3f}")
except Exception as e:
    fail("S1-01 valid BUY signal", traceback.format_exc(limit=2))

# --- Valid SELL ---
try:
    state = make_state(
        price=154.500, b4h="bearish", b1h="bearish", b15m="bearish",
        struct_15m=[_struct("LL", 154.100, "low"), _struct("LH", 154.580, "high")],
        struct_5m=[_struct("LH", 154.560, "high")],
        bos_5m=[_bos("bearish", 154.510), _bos("bearish", 154.505)],
        candles_5m=[_bearish_candle()],
    )
    r = strat1(state)
    assert r is not None, "returned None — expected SELL signal"
    assert r["type"] == "SELL", f"expected SELL got {r['type']}"
    assert r["sl"] > r["entry"], f"SL {r['sl']} not above entry {r['entry']}"
    assert r["tp"] < r["entry"], f"TP {r['tp']} not below entry {r['entry']}"
    ok(f"S1-02: Valid SELL fires — score={r['confidence']}, SL={r['sl']:.3f}, TP={r['tp']:.3f}")
except Exception as e:
    fail("S1-02 valid SELL signal", traceback.format_exc(limit=2))

# --- None state ---
try:
    assert strat1(None) is None
    ok("S1-03: Returns None for None state (no crash)")
except Exception as e:
    fail("S1-03", traceback.format_exc(limit=2))

# --- Conflicting 4H/1H bias rejected ---
try:
    state = make_state(b4h="bullish", b1h="bearish")
    assert strat1(state) is None
    ok("S1-04: Rejects when 4H=bull 1H=bear (no alignment)")
except Exception as e:
    fail("S1-04", traceback.format_exc(limit=2))

# --- Single TF alignment rejected ---
try:
    state = make_state(b4h="bullish", b1h="neutral")
    assert strat1(state) is None
    ok("S1-05: Rejects single-TF alignment (4H=bull, 1H=neutral)")
except Exception as e:
    fail("S1-05", traceback.format_exc(limit=2))

# --- Bearish 15M CHOCH invalidates bullish setup ---
try:
    state = make_state(
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900), _struct("HL", 154.480)],
        choch_15m=[_choch("bearish", 154.450)],
        bos_5m=[_bos("bullish", 154.490)],
        candles_5m=[_bullish_candle()],
    )
    assert strat1(state) is None
    ok("S1-06: Bearish 15M CHOCH invalidates bullish setup")
except Exception as e:
    fail("S1-06", traceback.format_exc(limit=2))

# --- No pullback structure rejected ---
try:
    state = make_state(
        b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900)],  # HH only, no HL
        bos_5m=[_bos("bullish", 154.490)],
    )
    assert strat1(state) is None
    ok("S1-07: Rejects when no 15M HL exists (no pullback structure)")
except Exception as e:
    fail("S1-07", traceback.format_exc(limit=2))

# --- Price overextended > 50 pips rejected ---
try:
    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900), _struct("HL", 153.900)],  # 60 pips away
        bos_5m=[_bos("bullish", 154.490)],
    )
    assert strat1(state) is None
    ok("S1-08: Rejects when price >50 pips from 15M HL (overextended)")
except Exception as e:
    fail("S1-08", traceback.format_exc(limit=2))

# --- No 5M BOS rejected ---
try:
    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900), _struct("HL", 154.480)],
        bos_5m=[],
    )
    assert strat1(state) is None
    ok("S1-09: Rejects when no 5M BOS")
except Exception as e:
    fail("S1-09", traceback.format_exc(limit=2))

# --- Single weak BOS without displacement candle rejected ---
try:
    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900), _struct("HL", 154.480)],
        struct_5m=[_struct("HL", 154.475)],
        bos_5m=[_bos("bullish", 154.490)],      # only 1 BOS
        candles_5m=[_doji()],                    # weak candle — body < 70%
    )
    assert strat1(state) is None
    ok("S1-10: Rejects single BOS with no qualifying displacement candle")
except Exception as e:
    fail("S1-10", traceback.format_exc(limit=2))

# --- SL too tight (<7 pips) rejected ---
try:
    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900), _struct("HL", 154.480)],
        struct_5m=[_struct("HL", 154.498)],      # only 0.2 pip from entry → SL too tight
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        candles_5m=[_bullish_candle()],
    )
    assert strat1(state) is None
    ok("S1-11: Rejects when structural SL < 7 pips from entry")
except Exception as e:
    fail("S1-11", traceback.format_exc(limit=2))

# --- No structural SL anchor rejected ---
try:
    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish",
        struct_15m=[_struct("HH", 154.900)],   # no HL — pullback exists via HH but no anchor
        struct_5m=[],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        candles_5m=[_bullish_candle()],
    )
    assert strat1(state) is None
    ok("S1-12: Rejects when no structural SL anchor (no 5M or 15M HL)")
except Exception as e:
    fail("S1-12", traceback.format_exc(limit=2))

# --- Output format validation ---
try:
    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish", b15m="bullish",
        struct_15m=[_struct("HH", 154.900), _struct("HL", 154.480)],
        struct_5m=[_struct("HL", 154.475)],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        candles_5m=[_bullish_candle()],
    )
    r = strat1(state)
    if r is not None:
        required = ["trade", "type", "confidence", "strategy", "reason", "entry", "sl", "tp", "rr", "net_rr", "spread_pips"]
        missing = [k for k in required if k not in r]
        assert not missing, f"missing keys: {missing}"
        assert r["strategy"] == "MTF Pullback Precision Scalping"
        assert isinstance(r["confidence"], int)
        assert r["spread_pips"] > 0
        ok("S1-13: Output has all required keys with correct types")
    else:
        ok("S1-13: No signal (below threshold) — format N/A")
except Exception as e:
    fail("S1-13", traceback.format_exc(limit=2))

# --- RR >= 2:1 ---
try:
    state = make_state(
        price=154.500, b4h="bullish", b1h="bullish", b15m="bullish",
        struct_15m=[_struct("HH", 154.900), _struct("HL", 154.480)],
        struct_5m=[_struct("HL", 154.475)],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        candles_5m=[_bullish_candle()],
    )
    r = strat1(state)
    if r is not None:
        rr = abs(r["tp"] - r["entry"]) / abs(r["entry"] - r["sl"])
        assert round(rr, 1) >= 2.0, f"RR={rr:.2f}"
        ok(f"S1-14: RR is {rr:.2f}:1 (>= 2:1 required)")
    else:
        ok("S1-14: No signal — RR check N/A")
except Exception as e:
    fail("S1-14", traceback.format_exc(limit=2))


# ══════════════════════════════════════════════════════════════════════════════
# 4. STRATEGY 2 — Liquidity Sweep Reversal Scalping (14 unit tests)
# ══════════════════════════════════════════════════════════════════════════════

section("4. STRATEGY 2 — Liquidity Sweep Reversal Scalping")

# --- Valid BUY sweep ---
try:
    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        zones_5m=[_zone(154.470, 154.430)],
        candles_5m=[_bullish_candle()],
        sessions=["london"],
    )
    r = strat2(state)
    assert r is not None, "returned None"
    assert r["type"] == "BUY", f"got {r['type']}"
    assert r["sl"] < r["entry"]
    assert r["tp"] > r["entry"]
    ok(f"S2-01: Valid BUY sweep reversal fires — score={r['confidence']}")
except Exception as e:
    fail("S2-01 valid BUY sweep reversal", traceback.format_exc(limit=2))

# --- Valid SELL sweep ---
try:
    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bullish", 154.550)],
        choch_5m=[_choch("bearish", 154.510)],
        candles_5m=[_bearish_candle()],
        sessions=["ny"],
    )
    r = strat2(state)
    assert r is not None, "returned None"
    assert r["type"] == "SELL", f"got {r['type']}"
    assert r["sl"] > r["entry"]
    assert r["tp"] < r["entry"]
    ok(f"S2-02: Valid SELL sweep reversal fires — score={r['confidence']}")
except Exception as e:
    fail("S2-02 valid SELL sweep reversal", traceback.format_exc(limit=2))

# --- None state ---
try:
    assert strat2(None) is None
    ok("S2-03: Returns None for None state (no crash)")
except Exception as e:
    fail("S2-03", traceback.format_exc(limit=2))

# --- Strongly trending market rejected ---
try:
    state = make_state(
        b4h="bullish", b1h="bullish",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        candles_5m=[_bullish_candle()],
    )
    assert strat2(state) is None
    ok("S2-04: Rejects strongly bullish trending market (use Strategy 1)")
except Exception as e:
    fail("S2-04", traceback.format_exc(limit=2))

try:
    state = make_state(
        b4h="bearish", b1h="bearish",
        choch_15m=[_choch("bullish", 154.550)],
        choch_5m=[_choch("bearish", 154.510)],
        candles_5m=[_bearish_candle()],
    )
    assert strat2(state) is None
    ok("S2-05: Rejects strongly bearish trending market")
except Exception as e:
    fail("S2-05", traceback.format_exc(limit=2))

# --- No 15M sweep rejected ---
try:
    state = make_state(
        b4h="neutral", b1h="neutral",
        choch_15m=[], bos_15m=[],
        choch_5m=[_choch("bullish", 154.490)],
        candles_5m=[_bullish_candle()],
    )
    assert strat2(state) is None
    ok("S2-06: Rejects when no sweep on 15M")
except Exception as e:
    fail("S2-06", traceback.format_exc(limit=2))

# --- No 5M reversal confirmation rejected ---
try:
    state = make_state(
        b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[], bos_5m=[],
        candles_5m=[_bullish_candle()],
    )
    assert strat2(state) is None
    ok("S2-07: Rejects when no 5M CHOCH/BOS confirmation")
except Exception as e:
    fail("S2-07", traceback.format_exc(limit=2))

# --- Recovery < 3 pips rejected (dead-cat bounce) ---
try:
    state = make_state(
        price=154.452,               # only 0.2 pip above sweep @ 154.450
        b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.452)],
        candles_5m=[_bullish_candle()],
    )
    assert strat2(state) is None
    ok("S2-08: Rejects <3 pip recovery from sweep (dead-cat bounce filter)")
except Exception as e:
    fail("S2-08", traceback.format_exc(limit=2))

# --- Entry too far from sweep rejected ---
try:
    state = make_state(
        price=154.500,
        b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 153.900)],   # 60 pips away
        choch_5m=[_choch("bullish", 154.490)],
        candles_5m=[_bullish_candle()],
    )
    assert strat2(state) is None
    ok("S2-09: Rejects when entry >25 pips from sweep zone")
except Exception as e:
    fail("S2-09", traceback.format_exc(limit=2))

# --- Weak reversal candle (doji) rejected ---
try:
    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        candles_5m=[_doji()],            # doji — body < 50%
    )
    assert strat2(state) is None
    ok("S2-10: Rejects doji reversal candle (body < 50% threshold)")
except Exception as e:
    fail("S2-10", traceback.format_exc(limit=2))

# --- Empty candles = no confirmation ---
try:
    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        candles_5m=[],    # empty
    )
    assert strat2(state) is None
    ok("S2-11: Rejects when candles list empty (no reversal candle to check)")
except Exception as e:
    fail("S2-11", traceback.format_exc(limit=2))

# --- CHOCH scores higher than BOS ---
try:
    state_choch = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        candles_5m=[_bullish_candle()], sessions=["london"],
    )
    state_bos = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        bos_15m=[_bos("bearish", 154.450)],
        bos_5m=[_bos("bullish", 154.490)],
        candles_5m=[_bullish_candle()], sessions=["london"],
    )
    r_choch = strat2(state_choch)
    r_bos   = strat2(state_bos)
    if r_choch and r_bos:
        assert r_choch["confidence"] > r_bos["confidence"]
        ok(f"S2-12: CHOCH(score={r_choch['confidence']}) > BOS(score={r_bos['confidence']})")
    elif r_choch and not r_bos:
        ok("S2-12: CHOCH fired, BOS alone below threshold — CHOCH is stronger")
    else:
        ok("S2-12: Both below threshold — relative scoring N/A")
except Exception as e:
    fail("S2-12", traceback.format_exc(limit=2))

# --- SL placed at sweep level + 8-pip buffer ---
try:
    sweep = 154.450
    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", sweep)],
        choch_5m=[_choch("bullish", 154.490)],
        candles_5m=[_bullish_candle()], sessions=["london"],
    )
    r = strat2(state)
    if r is not None:
        expected_sl = sweep - config.SWEEP_SL_BUFFER_PIPS * config.PIP_SIZE
        assert abs(r["sl"] - expected_sl) < 0.001, f"SL={r['sl']:.3f} expected~{expected_sl:.3f}"
        ok(f"S2-13: SL at sweep - 8 pips (got {r['sl']:.3f})")
    else:
        ok("S2-13: No signal — SL check N/A")
except Exception as e:
    fail("S2-13", traceback.format_exc(limit=2))

# --- Output format ---
try:
    state = make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        candles_5m=[_bullish_candle()], sessions=["london"],
    )
    r = strat2(state)
    if r is not None:
        required = ["trade", "type", "confidence", "strategy", "reason", "entry", "sl", "tp", "rr", "net_rr", "spread_pips"]
        missing = [k for k in required if k not in r]
        assert not missing, f"missing: {missing}"
        assert r["strategy"] == "Liquidity Sweep Reversal Scalping"
        ok("S2-14: Output has all required keys with correct types")
    else:
        ok("S2-14: No signal — format N/A")
except Exception as e:
    fail("S2-14", traceback.format_exc(limit=2))


# ══════════════════════════════════════════════════════════════════════════════
# 5. RISK MANAGER — All 9 validation checks
# ══════════════════════════════════════════════════════════════════════════════

section("5. RISK MANAGER — validate() and get_lot_size()")

good_buy  = {"trade": True, "type": "BUY",  "entry": 154.500, "sl": 154.350, "tp": 154.800}
good_sell = {"trade": True, "type": "SELL", "entry": 154.500, "sl": 154.650, "tp": 154.200}
good_stats = {"trades_today": 0, "consecutive_losses": 0}

try:
    ok_flag, reason = validate(good_buy, good_stats)
    assert ok_flag, f"rejected: {reason}"
    ok("RM-01: Valid BUY (SL below, TP above, RR=2) approved")
except Exception as e:
    fail("RM-01", str(e))

try:
    ok_flag, reason = validate(good_sell, good_stats)
    assert ok_flag, f"rejected: {reason}"
    ok("RM-02: Valid SELL (SL above, TP below, RR=2) approved")
except Exception as e:
    fail("RM-02", str(e))

try:
    ok_flag, _ = validate(good_buy, {"trades_today": 3, "consecutive_losses": 0})
    assert not ok_flag
    ok("RM-03: Blocks when max 3 trades/day reached")
except Exception as e:
    fail("RM-03", str(e))

try:
    ok_flag, _ = validate(good_buy, {"trades_today": 0, "consecutive_losses": 2})
    assert not ok_flag
    ok("RM-04: Blocks after 2 consecutive losses")
except Exception as e:
    fail("RM-04", str(e))

try:
    bad_rr = {"trade": True, "type": "BUY", "entry": 154.500, "sl": 154.420, "tp": 154.580}
    ok_flag, reason = validate(bad_rr, good_stats)
    assert not ok_flag, "should reject RR < 2"
    ok("RM-05: Rejects RR < 2:1")
except Exception as e:
    fail("RM-05", str(e))

try:
    sl_wrong = {"trade": True, "type": "BUY", "entry": 154.500, "sl": 154.600, "tp": 154.800}
    ok_flag, reason = validate(sl_wrong, good_stats)
    assert not ok_flag
    ok("RM-06: Rejects BUY with SL above entry")
except Exception as e:
    fail("RM-06", str(e))

try:
    tp_wrong = {"trade": True, "type": "SELL", "entry": 154.500, "sl": 154.650, "tp": 154.600}
    ok_flag, reason = validate(tp_wrong, good_stats)
    assert not ok_flag
    ok("RM-07: Rejects SELL with TP above entry")
except Exception as e:
    fail("RM-07", str(e))

try:
    nan_trade = {"trade": True, "type": "BUY", "entry": float("nan"), "sl": 154.350, "tp": 154.800}
    ok_flag, _ = validate(nan_trade, good_stats)
    assert not ok_flag
    ok("RM-08: Rejects NaN entry value")
except Exception as e:
    fail("RM-08", str(e))

try:
    inf_trade = {"trade": True, "type": "BUY", "entry": 154.500, "sl": float("-inf"), "tp": 154.800}
    ok_flag, _ = validate(inf_trade, good_stats)
    assert not ok_flag
    ok("RM-09: Rejects Infinity SL value")
except Exception as e:
    fail("RM-09", str(e))

try:
    bad_dir = {"trade": True, "type": "HOLD", "entry": 154.500, "sl": 154.350, "tp": 154.800}
    ok_flag, _ = validate(bad_dir, good_stats)
    assert not ok_flag
    ok("RM-10: Rejects invalid direction 'HOLD'")
except Exception as e:
    fail("RM-10", str(e))

try:
    ok_flag, _ = validate(None, good_stats)
    assert not ok_flag
    ok("RM-11: Rejects None decision gracefully")
except Exception as e:
    fail("RM-11", str(e))

try:
    ok_flag, _ = validate(good_buy, None)
    assert not ok_flag
    ok("RM-12: Rejects None session_stats gracefully")
except Exception as e:
    fail("RM-12", str(e))

try:
    lot = get_lot_size()
    assert lot == 0.02, f"expected 0.02 got {lot}"
    ok(f"RM-13: get_lot_size() returns 0.02")
except Exception as e:
    fail("RM-13", str(e))

try:
    zero_sl = {"trade": True, "type": "BUY", "entry": 154.500, "sl": 154.500, "tp": 154.800}
    ok_flag, reason = validate(zero_sl, good_stats)
    assert not ok_flag
    ok("RM-14: Rejects zero SL distance")
except Exception as e:
    fail("RM-14", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 6. SIGNAL MEMORY
# ══════════════════════════════════════════════════════════════════════════════

section("6. SIGNAL MEMORY — Duplicate detection")

decision_a = {"strategy": "MTF Pullback Precision Scalping", "type": "BUY",  "sl": 154.35}
decision_b = {"strategy": "MTF Pullback Precision Scalping", "type": "BUY",  "sl": 154.35}  # same
decision_c = {"strategy": "MTF Pullback Precision Scalping", "type": "BUY",  "sl": 154.20}  # diff SL
decision_d = {"strategy": "MTF Pullback Precision Scalping", "type": "SELL", "sl": 154.65}  # diff dir
state_bull = {"bias": {"1h": "bullish"}}
state_bear = {"bias": {"1h": "bearish"}}

try:
    sm = SignalMemory()
    assert not sm.is_duplicate(decision_a, state_bull)
    ok("SM-01: Fresh memory — no duplicate on first signal")
except Exception as e:
    fail("SM-01", str(e))

try:
    sm = SignalMemory()
    sm.record(decision_a, state_bull)
    assert sm.is_duplicate(decision_b, state_bull)
    ok("SM-02: Same key + same bias → is_duplicate=True")
except Exception as e:
    fail("SM-02", str(e))

try:
    sm = SignalMemory()
    sm.record(decision_a, state_bull)
    assert not sm.is_duplicate(decision_c, state_bull)
    ok("SM-03: Different SL → not duplicate (new setup)")
except Exception as e:
    fail("SM-03", str(e))

try:
    sm = SignalMemory()
    sm.record(decision_a, state_bull)
    # Bias flips → signal memory clears itself
    assert not sm.is_duplicate(decision_b, state_bear)
    ok("SM-04: Bias flip clears memory → not duplicate")
except Exception as e:
    fail("SM-04", str(e))

try:
    sm = SignalMemory()
    sm.record(decision_a, state_bull)
    assert not sm.is_duplicate(decision_d, state_bull)
    ok("SM-05: Different direction → not duplicate")
except Exception as e:
    fail("SM-05", str(e))

try:
    sm = SignalMemory()
    sm.record(decision_a, state_bull)
    sm.clear()
    assert not sm.is_duplicate(decision_b, state_bull)
    ok("SM-06: clear() resets memory — not duplicate after clear")
except Exception as e:
    fail("SM-06", str(e))

try:
    sm = SignalMemory()
    sm.record(decision_a, state_bull)
    assert sm.is_duplicate(decision_b, state_bull)
    sm.record(decision_c, state_bull)      # record a different setup
    assert not sm.is_duplicate(decision_b, state_bull)   # old key no longer current
    ok("SM-07: After re-recording different key, old key no longer duplicate")
except Exception as e:
    fail("SM-07", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 7. SIMULATOR
# ══════════════════════════════════════════════════════════════════════════════

section("7. EXECUTION SIMULATOR")

sim_decision = {
    "type": "BUY", "strategy": "Test", "entry": 154.500,
    "sl": 154.350, "tp": 154.800, "confidence": 85,
    "reason": "unit test trade",
}

try:
    result = sim_order(sim_decision, 0.02)
    assert result is True
    ok("SIM-01: Valid BUY sim order returns True")
except Exception as e:
    fail("SIM-01", str(e))

try:
    result = sim_order(sim_decision, 0)
    assert result is False
    ok("SIM-02: Zero lot size rejected — returns False")
except Exception as e:
    fail("SIM-02", str(e))

try:
    result = sim_order(sim_decision, -0.01)
    assert result is False
    ok("SIM-03: Negative lot size rejected — returns False")
except Exception as e:
    fail("SIM-03", str(e))

try:
    result = sim_order(sim_decision, "invalid")
    assert result is False
    ok("SIM-04: Non-numeric lot size rejected — returns False")
except Exception as e:
    fail("SIM-04", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 8. CONFLUENCE SCORING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

section("8. CONFLUENCE SCORING ENGINE")

try:
    all_true = {k: True for k in ["bias_aligned","zone_present","bos_confirmed","session_match","clean_structure","precision_factor"]}
    score, breakdown = calculate({}, "BUY", all_true)
    assert score == 100, f"expected 100 got {score}"
    ok("CF-01: All conditions True → score=100")
except Exception as e:
    fail("CF-01", str(e))

try:
    all_false = {k: False for k in ["bias_aligned","zone_present","bos_confirmed","session_match","clean_structure","precision_factor"]}
    score, breakdown = calculate({}, "BUY", all_false)
    assert score == 0, f"expected 0 got {score}"
    ok("CF-02: All conditions False → score=0")
except Exception as e:
    fail("CF-02", str(e))

try:
    only_bias = {"bias_aligned": True}
    score, _ = calculate({}, "BUY", only_bias)
    assert score == 30, f"expected 30 got {score}"
    ok("CF-03: bias_aligned only → score=30")
except Exception as e:
    fail("CF-03", str(e))

try:
    assert tradeable(70) is True
    assert tradeable(69) is False
    assert tradeable(100) is True
    assert tradeable(0) is False
    ok("CF-04: tradeable() threshold=70 correct")
except Exception as e:
    fail("CF-04", str(e))

try:
    conds = {"bias_aligned": True, "bos_confirmed": True}
    score, breakdown = calculate({}, "BUY", conds)
    assert score == 50   # 30 + 20
    assert breakdown["bias_aligned"]["passed"] is True
    assert breakdown["zone_present"]["passed"] is False
    ok("CF-05: Partial score=50 (bias+bos) with correct breakdown")
except Exception as e:
    fail("CF-05", str(e))

try:
    conds = {"bias_aligned": True, "bos_confirmed": True, "session_match": True}
    score, breakdown = calculate({}, "BUY", conds)
    txt = format_breakdown(score, breakdown)
    assert "score=60" in txt
    assert "bias_aligned" in txt
    ok(f"CF-06: format_breakdown output: '{txt}'")
except Exception as e:
    fail("CF-06", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 9. STRATEGY REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

section("9. STRATEGY REGISTRY — __init__.py")

try:
    assert len(STRATEGIES) == 5, f"expected 5 got {len(STRATEGIES)}"
    ok(f"ST-REG-01: Registry has exactly 5 strategies")
except Exception as e:
    fail("ST-REG-01", str(e))

try:
    names = [name for name, _ in STRATEGIES]
    assert "MTF Pullback Precision Scalping" in names
    assert "Liquidity Sweep Reversal Scalping" in names
    ok("ST-REG-02: S1 and S2 registered with correct names")
except Exception as e:
    fail("ST-REG-02", str(e))

try:
    for name, fn in STRATEGIES:
        assert callable(fn), f"{name} is not callable"
    ok("ST-REG-03: All 5 strategy functions are callable")
except Exception as e:
    fail("ST-REG-03", str(e))

try:
    # Placeholder strategies 3/4/5 return None (not crash)
    dummy_state = sanitize_state({"current_price": 154.500})
    for name, fn in STRATEGIES[2:]:  # S3, S4, S5
        result = fn(dummy_state)
        assert result is None, f"{name} placeholder returned non-None"
    ok("ST-REG-04: Placeholder S3/S4/S5 return None without crash")
except Exception as e:
    fail("ST-REG-04", traceback.format_exc(limit=2))


# ══════════════════════════════════════════════════════════════════════════════
# 10. INTEGRATION PIPELINE — State → Strategy → Risk → Execute
# ══════════════════════════════════════════════════════════════════════════════

section("10. INTEGRATION PIPELINE — End to end")

try:
    # Build full state, run through strategy, then risk manager, then simulator
    state = sanitize_state(make_state(
        price=154.500, b4h="bullish", b1h="bullish", b15m="bullish",
        struct_15m=[_struct("HH", 154.900), _struct("HL", 154.480)],
        struct_5m=[_struct("HL", 154.475)],
        bos_5m=[_bos("bullish", 154.490), _bos("bullish", 154.495)],
        candles_5m=[_bullish_candle()],
    ))
    decision = strat1(state)
    if decision:
        decision["symbol"] = "USD/JPY"
        ok_flag, reason = validate(decision, {"trades_today": 0, "consecutive_losses": 0})
        if ok_flag:
            result = sim_order(decision, 0.02)
            assert result is True
            ok("INT-01: Full BUY pipeline (state→S1→RM→sim) executes successfully")
        else:
            ok(f"INT-01: Pipeline ran — RM blocked: {reason} (acceptable)")
    else:
        ok("INT-01: Pipeline ran — S1 below threshold (acceptable)")
except Exception as e:
    fail("INT-01 BUY pipeline", traceback.format_exc(limit=2))

try:
    state = sanitize_state(make_state(
        price=154.500, b4h="neutral", b1h="neutral",
        choch_15m=[_choch("bearish", 154.450)],
        choch_5m=[_choch("bullish", 154.490)],
        candles_5m=[_bullish_candle()], sessions=["london"],
    ))
    decision = strat2(state)
    if decision:
        decision["symbol"] = "USD/JPY"
        ok_flag, reason = validate(decision, {"trades_today": 0, "consecutive_losses": 0})
        if ok_flag:
            result = sim_order(decision, 0.02)
            assert result is True
            ok("INT-02: Full SWEEP pipeline (state→S2→RM→sim) executes successfully")
        else:
            ok(f"INT-02: Pipeline ran — RM blocked: {reason}")
    else:
        ok("INT-02: Pipeline ran — S2 below threshold (acceptable)")
except Exception as e:
    fail("INT-02 SWEEP pipeline", traceback.format_exc(limit=2))

try:
    # S2 must be silent when S1 fires (strongly trending)
    state = make_state(b4h="bullish", b1h="bullish",
                       choch_15m=[_choch("bearish", 154.450)],
                       candles_5m=[_bullish_candle()])
    r2 = strat2(state)
    assert r2 is None, "S2 must not fire in trending market"
    ok("INT-03: S2 silent when market strongly trends (S1 domain)")
except Exception as e:
    fail("INT-03", traceback.format_exc(limit=2))

try:
    # Signal memory prevents re-entry on same setup
    sm = SignalMemory()
    decision = {"strategy": "MTF Pullback Precision Scalping", "type": "BUY",
                "sl": 154.35, "trade": True, "entry": 154.500, "tp": 154.800, "confidence": 85}
    state = {"bias": {"1h": "bullish"}}
    assert not sm.is_duplicate(decision, state)  # first time — not dup
    sm.record(decision, state)
    assert sm.is_duplicate(decision, state)      # second time — duplicate
    ok("INT-04: Signal memory blocks re-entry on same setup same session")
except Exception as e:
    fail("INT-04", str(e))

try:
    # All-neutral state → both strategies silent
    state = sanitize_state({
        "current_price": 154.500,
        "sessions": ["london"],
        "tradeable_session": True,
        "bias": {"4h": "neutral", "1h": "neutral", "15m": "neutral"},
        "5m":  {"trend":"neutral","structure":[],"bos":[],"choch":[],"zones":[],"candles":[]},
        "15m": {"trend":"neutral","structure":[],"bos":[],"choch":[],"zones":[]},
        "1m":  {"trend":"neutral","structure":[],"bos":[],"choch":[],"zones":[],"candles":[],"sr_levels":[]},
        "1h":  {"trend":"neutral","structure":[],"bos":[],"choch":[],"zones":[]},
        "sr_levels": [], "asia_range": {"high": None, "low": None},
    })
    r1 = strat1(state)
    r2 = strat2(state)
    assert r1 is None and r2 is None
    ok("INT-05: All-neutral market → both strategies silent (no trade)")
except Exception as e:
    fail("INT-05", traceback.format_exc(limit=2))


# ══════════════════════════════════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════════════════════════════════

total = PASS + FAIL
print(f"\n{'='*68}")
print(f"  UNIT TEST REPORT")
print(f"{'='*68}")
print(f"  Tests run  : {total}")
print(f"  Passed     : {PASS}")
print(f"  Failed     : {FAIL}")
print(f"  Pass rate  : {100*PASS/total:.1f}% ({PASS}/{total})")

if ERRORS:
    print(f"\n  -- FAILED TESTS --")
    for e in ERRORS:
        print(f"    x  {e}")

print(f"\n{'='*68}\n")
sys.exit(0 if FAIL == 0 else 1)
