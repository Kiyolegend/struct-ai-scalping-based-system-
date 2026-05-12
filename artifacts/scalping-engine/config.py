STRUCT_API_BASE = "http://localhost:8001/trading-api"

# â”€â”€ Active symbol (change this to switch which pair the engine scans) â”€â”€â”€â”€â”€â”€â”€â”€â”€
SYMBOL = "USD/JPY"

# â”€â”€ Symbol table â€” pip sizes and MT5 names for each supported pair â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# pip_size : value of 1 pip in price terms
# mt5_name : exact symbol name in your MT5 Market Watch
# digits   : decimal places shown on chart
SYMBOL_CONFIG = {
    # spread_pips      : typical broker spread in pips (MetaQuotes-Demo, conservative estimate)
    # pip_value_per_lot: approx USD value of 1 pip per 1.0 standard lot (used by Trade Journal P&L)
    #   JPY pairs:  ~$6.50  (0.01 * 100,000 / ~154 USDJPY)
    #   USD-quoted: $10.00  (0.0001 * 100,000, exact)
    #   USDCAD:     ~$7.40  (0.0001 * 100,000 / ~1.35 CADUSD)
    #   USDCHF:     ~$11.25 (0.0001 * 100,000 / ~0.89 CHFUSD)
    "USD/JPY": {"mt5_name": "USDJPYm", "pip_size": 0.01,   "digits": 3, "spread_pips": 1.0, "pip_value_per_lot": 6.50},
    "EUR/USD": {"mt5_name": "EURUSDm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.0, "pip_value_per_lot": 10.00},
    "GBP/USD": {"mt5_name": "GBPUSDm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.2, "pip_value_per_lot": 10.00},
    "EUR/JPY": {"mt5_name": "EURJPYm", "pip_size": 0.01,   "digits": 3, "spread_pips": 1.4, "pip_value_per_lot": 6.50},
    "GBP/JPY": {"mt5_name": "GBPJPYm", "pip_size": 0.01,   "digits": 3, "spread_pips": 2.5, "pip_value_per_lot": 6.50},
    "AUD/USD": {"mt5_name": "AUDUSDm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.2, "pip_value_per_lot": 10.00},
    "USD/CAD": {"mt5_name": "USDCADm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.5, "pip_value_per_lot": 7.40},
    "USD/CHF": {"mt5_name": "USDCHFm", "pip_size": 0.0001, "digits": 5, "spread_pips": 1.5, "pip_value_per_lot": 11.25},
}

def get_spread_pips(symbol: str = None) -> float:
    """Return the typical spread in pips for a symbol."""
    return SYMBOL_CONFIG.get(symbol or SYMBOL, SYMBOL_CONFIG["USD/JPY"]).get("spread_pips", 1.0)

# â”€â”€ Symbols to scan every cycle (remove any you don't want) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
SCAN_SYMBOLS = list(SYMBOL_CONFIG.keys())

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
RISK_PERCENT = 0.01
MAX_RISK_PERCENT = 0.03
CONTRACT_SIZE = 100000
MIN_RR = 2.0
# Minimum RR after deducting spread cost.  Net RR = (TP_pips - spread) / (SL_pips + spread).
# Trades where spread eats too deep into profit are rejected before reaching MT5.
NET_MIN_RR = 1.5

TARGET_RR  = 2.0
MAX_TRADES_PER_DAY = 3
MAX_CONSECUTIVE_LOSSES = 2

# â”€â”€ Minimum confidence score to fire a trade (both strategy-level and engine-level) â”€â”€
# Signals scoring below this are completely suppressed â€” never reach the risk manager.
# Raise this number to be more selective. 80 = high-quality setups only.
MIN_CONFIDENCE = 80

LOOP_INTERVAL = 30

SIMULATION_MODE = True

NEAR_LEVEL_PIPS = 10
PIP_SIZE = get_symbol_cfg()["pip_size"]
# Minimum SL distance in pips â€” signals with a tighter stop are rejected.
# 7 = default (allows tight structural setups).
# Raise to 10 if you're getting stopped out by noise on USDJPY during London.
MIN_SL_PIPS = 7
SL_BUFFER_PIPS = 5
# Wider buffer for Strategy 2 sweeps â€” liquidity grabs routinely re-test the level
# (double-wick), so a 5-pip buffer often gets stopped out before the real reversal.
SWEEP_SL_BUFFER_PIPS = 8
# Minimum pips price must have recovered beyond the sweep level before entry is valid.
# Filters dead-cat bounces where the recovery is only 1â€“2 ticks.
MIN_SWEEP_RECOVERY_PIPS = 3
