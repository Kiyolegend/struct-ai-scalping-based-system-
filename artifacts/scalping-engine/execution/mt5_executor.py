"""
MT5 Executor — sends real orders to MetaTrader 5.

Used when SIMULATION_MODE = False.
Requires the MetaTrader5 Python package and MT5 terminal to be open and logged in.

Optional environment variables (only needed if you want the engine to log in
programmatically rather than using the already-logged-in MT5 terminal):
  MT5_LOGIN    — account number (integer)
  MT5_PASSWORD — account password
  MT5_SERVER   — broker server name (e.g. "ICMarkets-Live01")

Normal usage: just open MT5, log in manually, then start the engine.
The engine will connect to the already-running terminal automatically.
"""

import os
import sys
import os.path as _p
sys.path.insert(0, _p.join(_p.dirname(__file__), ".."))
import config


def _connect():
    """Connect to the already-running MT5 terminal.

    Primary path: MT5 is already open and logged in — just call initialize().
    Fallback path: If MT5_LOGIN + MT5_PASSWORD env vars are set, log in
                   programmatically (useful when running the engine headlessly).
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("  [ERROR] MetaTrader5 package not installed. Run: pip install MetaTrader5")
        return None

    if not mt5.initialize():
        print(f"  [ERROR] MT5 initialize() failed: {mt5.last_error()}")
        print("          Is MetaTrader 5 open on this machine?")
        return None

    # If the terminal is already logged in, we are done.
    account_info = mt5.account_info()
    if account_info is not None:
        return mt5  # already connected and authenticated

    # Terminal is running but not logged in — try env-var credentials
    login    = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server   = os.getenv("MT5_SERVER", "")

    if not login or not password:
        print("  [ERROR] MT5 is running but not logged in, and no credentials found.")
        print("          Either log into MT5 manually before starting the engine,")
        print("          or set MT5_LOGIN / MT5_PASSWORD environment variables.")
        mt5.shutdown()
        return None

    if not mt5.login(int(login), password=password, server=server):
        print(f"  [ERROR] MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return None

    return mt5


def place_order(decision: dict, lot: float) -> bool:
    """Send a market order to MT5. Returns True if filled.

    The symbol is taken from the decision dict (which carries the pair name
    that generated the signal), then looked up in SYMBOL_CONFIG to get the
    exact MT5 market-watch name.  This means multi-symbol scanning works
    correctly — each trade goes to the right instrument.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("  [ERROR] MetaTrader5 package not installed.")
        return False

    mt5_inst = _connect()
    if mt5_inst is None:
        return False

    try:
        # ── Resolve the correct MT5 symbol for this signal ────────────────
        signal_sym = decision.get("symbol", config.SYMBOL)
        sym_cfg    = config.get_symbol_cfg(signal_sym)
        mt5_symbol = sym_cfg["mt5_name"]   # e.g. "USDJPY", "EURUSD", "GBPJPY"

        trade_type = mt5.ORDER_TYPE_BUY if decision["type"] == "BUY" else mt5.ORDER_TYPE_SELL
        price_info = mt5.symbol_info_tick(mt5_symbol)

        if price_info is None:
            print(f"  [ERROR] Cannot get price for {mt5_symbol}.")
            print(f"          Check that {mt5_symbol} is visible in MT5 Market Watch.")
            return False

        fill_price = price_info.ask if decision["type"] == "BUY" else price_info.bid

        # Auto-detect the correct filling mode supported by this broker/symbol.
        # Different brokers support different modes — this avoids retcode 10030.
        sym_info = mt5.symbol_info(mt5_symbol)
        if sym_info is not None and sym_info.filling_mode & 1:
            filling = mt5.ORDER_FILLING_FOK      # Fill or Kill (most common on CFD brokers)
        elif sym_info is not None and sym_info.filling_mode & 2:
            filling = mt5.ORDER_FILLING_IOC      # Immediate or Cancel
        else:
            filling = mt5.ORDER_FILLING_RETURN   # Return remainder (exchange-style)
        print(f"  [MT5] Using filling mode: {filling} (broker filling_mode={getattr(sym_info, 'filling_mode', 'N/A')})")

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       mt5_symbol,
            "volume":       lot,
            "type":         trade_type,
            "price":        fill_price,
            "sl":           decision["sl"],
            "tp":           decision["tp"],
            "deviation":    10,
            "magic":        202401,
            "comment":      f"SCALP:{decision['strategy'][:10]}",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = mt5.order_send(request)

        if result is None:
            print(f"  [ERROR] MT5 order_send returned None: {mt5.last_error()}")
            return False

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"\n  ORDER FILLED  ticket={result.order}")
            print(f"  {decision['type']} {lot} lots {mt5_symbol} @ {fill_price:.5f}")
            print(f"  SL={decision['sl']:.5f}  TP={decision['tp']:.5f}\n")
            return True
        else:
            print(f"  [ERROR] Order failed: retcode={result.retcode} | {result.comment}")
            return False

    finally:
        mt5.shutdown()


def has_open_position(symbol: str) -> bool:
    """Check whether MT5 currently has any open position on this symbol.

    Called by run_engine_cycle before executing a new signal. If any open
    position exists on the symbol (BUY or SELL, any lot size), returns True
    and the engine skips the signal entirely — preventing hedges.

    Returns False on any connection error so the engine fails safe (i.e. if
    MT5 cannot be reached the position check is treated as "no position open"
    and normal execution continues — rather than silently blocking all trades).
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return False

    mt5_inst = _connect()
    if mt5_inst is None:
        return False

    try:
        sym_cfg    = config.get_symbol_cfg(symbol)
        mt5_symbol = sym_cfg["mt5_name"]          # e.g. "GBPUSDm", "EURUSDm"
        positions  = mt5.positions_get(symbol=mt5_symbol)
        if positions is None:
            return False
        return any(p.magic == 202401 for p in positions)

    finally:
        mt5.shutdown()