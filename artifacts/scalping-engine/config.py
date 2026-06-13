import time as _time
STRUCT_API_BASE = "http://localhost:8001/trading-api"

# ── Active symbol (change this to switch which pair the engine scans) ─────────
SYMBOL = "USD/JPY"

# ── Symbol table — pip sizes and MT5 names for each supported pair ────────────
# pip_size          : value of 1 pip in price terms
# mt5_name          : exact symbol name in your MT5 Market Watch
# digits            : decimal places shown on chart
# spread_pips       : typical broker spread in pips (conservative estimate)
# commission_pips   : Nexus mobile account execution cost per trade in pips
#                     (pip gap between master account signal and actual fill).
#                     Set these to the values you observe per pair.
#                     Total cost = spread_pips + commission_pips (used in net RR calc).
# pip_value_per_lot : approx USD value of 1 pip per 1.0 standard lot
#   JPY pairs:  ~$6.50  (0.01 * 100,000 / ~157 USDJPY)
#   USD-quoted: $10.00  (0.0001 * 100,000, exact)
#   USDCAD:     ~$7.30  (0.0001 * 100,000 / ~1.37 USDCAD)
#   USDCHF:     ~$11.10 (0.0001 * 100,000 / ~0.90 USDCHF)
#
# commission_pips: Exness fee converted to pips (at 0.02 lot, real Nexus fills)
#   How to calculate: fee_in_usd / pip_value_per_0.02lot
#   Example USDJPY:  $0.13 / (0.01 * 2000 / 157) = $0.13 / $0.127 = 1.0p
#   Example EURUSD:  $0.16 / (0.0001 * 2000)     = $0.16 / $0.20  = 0.8p
#   Update these if Exness changes fees or if you change lot size.
#
# total cost (spread + commission) per pair:
#   USD/JPY  1.0 + 1.0 = 2.0p  ✓ viable
#   EUR/USD  1.0 + 0.8 = 1.8p  ✓ best value
#   GBP/USD  1.2 + 1.0 = 2.2p  ✓ viable
#   EUR/JPY  1.4 + 1.6 = 3.0p  ✗ disabled — net RR 1.50 on 15p SL, below 1.6 min
#   GBP/JPY  3.5 + 2.2 = 5.7p  ✗ disabled — too volatile + too expensive
#   AUD/USD  1.2 + 0.9 = 2.1p  ✓ viable
#   USD/CAD  1.5 + 1.4 = 2.9p  ✗ disabled — net RR 1.51 on 15p SL, below 1.6 min
#   USD/CHF  1.5 + 0.7 = 2.2p  ✓ viable (fee estimated, update when confirmed)
SYMBOL_CONFIG = {
    "USD/JPY": {"mt5_name": "USDJPYm", "pip_size": 0.01,   "digits": 3, "spread_pips": 1.0, "commission_pips": 1.0, "pip_value_per_lot": 6.50},
    "EUR/USD": {"mt5_name": "EURUSDm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.0, "commission_pips": 0.8, "pip_value_per_lot": 10.00},
    "GBP/USD": {"mt5_name": "GBPUSDm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.2, "commission_pips": 1.0, "pip_value_per_lot": 10.00},
    "EUR/JPY": {"mt5_name": "EURJPYm", "pip_size": 0.01,   "digits": 3, "spread_pips": 1.4, "commission_pips": 1.6, "pip_value_per_lot": 6.50},
    "GBP/JPY": {"mt5_name": "GBPJPYm", "pip_size": 0.01,   "digits": 3, "spread_pips": 3.5, "commission_pips": 2.2, "pip_value_per_lot": 6.50},
    "AUD/USD": {"mt5_name": "AUDUSDm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.2, "commission_pips": 0.9, "pip_value_per_lot": 10.00},
    "USD/CAD": {"mt5_name": "USDCADm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.5, "commission_pips": 1.4, "pip_value_per_lot": 7.30},
    "USD/CHF": {"mt5_name": "USDCHFm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.5, "commission_pips": 0.7, "pip_value_per_lot": 11.10},
}

# ── Pairs disabled — either too expensive, too volatile, or net RR unworkable ─
# GBP/JPY: 5.7p total cost — most expensive pair, also most volatile. Always off.
# EUR/JPY: 3.0p total cost — at 15p SL the net RR is only 1.50, below our 1.6 min.
#          Would need 20p+ SL to get a clean net RR, which increases risk per trade.
# USD/CAD: 2.9p total cost — same problem, net RR 1.51 on a 15p SL.
#          Also slow-moving in Asian sessions, fewer quality setups anyway.
# Re-enable any of these by removing from the set below.
DISABLED_SYMBOLS = {"GBP/JPY", "EUR/JPY", "USD/CAD"}

def get_spread_pips(symbol: str = None) -> float:
    """Return the raw broker spread in pips for a symbol."""
    return SYMBOL_CONFIG.get(symbol or SYMBOL, SYMBOL_CONFIG["USD/JPY"]).get("spread_pips", 1.0)

def get_commission_pips(symbol: str = None) -> float:
    """Return the Nexus account pip gap/commission per trade for a symbol.
    Update these values when you observe the actual fill gap on each pair.
    """
    return SYMBOL_CONFIG.get(symbol or SYMBOL, SYMBOL_CONFIG["USD/JPY"]).get("commission_pips", 0.5)

def get_total_cost_pips(symbol: str = None) -> float:
    """Return total execution cost in pips = spread + commission.
    This is the true cost that net RR calculations should use.
    """
    return get_spread_pips(symbol) + get_commission_pips(symbol)

# ── Symbols to scan every cycle — disabled pairs are automatically excluded ───
SCAN_SYMBOLS = [s for s in SYMBOL_CONFIG.keys() if s not in DISABLED_SYMBOLS]

def get_symbol_cfg(symbol: str = None) -> dict:
    """Return pip_size, mt5_name, digits for the given symbol (defaults to SYMBOL)."""
    return SYMBOL_CONFIG.get(symbol or SYMBOL, SYMBOL_CONFIG["USD/JPY"])

MT5_SYMBOL = get_symbol_cfg()["mt5_name"]

ACCOUNT_BALANCE = 135.0
# 0.02 lot on USD/JPY = ~$0.133/pip = ~$3.87 risk per 15-pip SL (2.9% of $135)
# This targets $2-3/day at a realistic 50-55% win rate.
# Drop to 0.01 if you want to be more conservative while testing.
DEFAULT_LOT = 0.02
MAX_LOT = 0.05
MAX_RISK_PERCENT = 0.03
CONTRACT_SIZE = 100000
MIN_RR = 2.0

# Minimum RR after deducting TOTAL cost (spread + commission).
# Net RR = (TP_pips - total_cost) / (SL_pips + total_cost).
# Raised from 1.5 → 1.6 to account for the Nexus commission pip gap.
NET_MIN_RR = 1.5
# Add directly below it:
MAX_ENTRY_DRIFT_PIPS = 3   # skip order if live price has moved >3p from signal entry

TARGET_RR  = 2.0
MAX_TRADES_PER_DAY = 3
MAX_CONSECUTIVE_LOSSES = 2

# ── Minimum confidence score to fire a trade ─────────────────────────────────
# Raise to be more selective. 80 = high-quality setups only.
MIN_CONFIDENCE = 80

LOOP_INTERVAL = 12

SIMULATION_MODE = True

NEAR_LEVEL_PIPS = 10
PIP_SIZE = get_symbol_cfg()["pip_size"]

# Minimum SL distance in pips — signals with a tighter stop are rejected.
MIN_SL_PIPS = 7
SL_BUFFER_PIPS = 5

# Wider buffer for Strategy 2 sweeps — liquidity grabs routinely re-test the level
# (double-wick), so a 5-pip buffer often gets stopped before the real reversal.
SWEEP_SL_BUFFER_PIPS = 8

# Minimum pips price must have recovered beyond the sweep level before entry is valid.
# Filters dead-cat bounces where the recovery is only 1-2 ticks.
MIN_SWEEP_RECOVERY_PIPS = 5


def get_broker_ts(state: dict) -> int:
    try:
        candles = (state.get("5m") or {}).get("candles") or []
        if candles:
            t = int(candles[-1]["time"])
            if t > 1_000_000_000:
                return t
    except Exception:
        pass
    try:
        import ntplib as _ntplib
        c = _ntplib.NTPClient()
        r = c.request("pool.ntp.org", version=3, timeout=2)
        return int(r.tx_time)
    except Exception:
        pass
    return int(_time.time())


def fib_extension_tp(state: dict, direction: str, entry: float) -> float | None:
    """127.2% Fibonacci extension TP. Returns None if swing data missing — caller falls back to 2R."""
    try:
        hi = (state.get("4h") or {}).get("swing_hi")
        lo = (state.get("4h") or {}).get("swing_lo")
        if not hi or not lo or hi <= lo:
            return None
        rng = hi - lo
        tp  = (hi + 0.272 * rng) if direction == "bullish" else (lo - 0.272 * rng)
        if direction == "bullish" and tp <= entry: return None
        if direction == "bearish" and tp >= entry: return None
        return round(tp, 5)
    except Exception:
        return None