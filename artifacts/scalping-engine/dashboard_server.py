"""
STRUCT.ai Scalping Engine — Dashboard Server

Runs the scalping engine in a background thread and serves a live
browser dashboard on http://localhost:<PORT>

Execution TF :  5M
Confirmation : 15M
Bias         : 1H / 4H
"""

import os
import sys
import json
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import webbrowser
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

import config
from state import build_state, get_active_sessions
from strategies import STRATEGIES
from risk.manager import validate, get_lot_size
from execution.simulator import place_order as sim_order
from logger import log_trade
from signal_memory import SignalMemory
from news_filter_live import is_safe_to_trade, is_global_blocked, is_symbol_blocked, get_upcoming_blocked_days, get_pair_confidence_penalty

try:
    from execution.mt5_executor import place_order as live_order, has_open_position
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

SIM_FORCE  = "--sim"  in sys.argv
LIVE_FORCE = "--live" in sys.argv

PORT = int(os.environ.get("PORT", 5002))

app = Flask(__name__, template_folder="templates")

# ── Per-symbol controls (enabled toggle + one-shot force-fire) ───────────────
symbol_controls: dict[str, dict] = {
    sym: {"enabled": True, "force_fire": False}
    for sym in config.SCAN_SYMBOLS
}
controls_lock = threading.Lock()

# ── Shared state (written by engine thread, read by Flask) ──────────────────
engine_state = {
    "status":        "starting",
    "mode":          "SIMULATION" if (config.SIMULATION_MODE or SIM_FORCE) else "LIVE",
    "symbol":        config.SYMBOL,
    "scan_symbols":  config.SCAN_SYMBOLS,
    "price":         None,
    "bias":          {"4h": "—", "1h": "—", "15m": "—"},
    "sessions":      [],
    "asia_range":    {"high": None, "low": None},
    "bos_count":     0,
    "choch_count":   0,
    "zone_count":    0,
    "strategy_scores": [],
    "active_signal": None,
    "trade_log":     [],
    "trades_today":  0,
    "consecutive_losses": 0,
    "last_update":   None,
    "next_scan_in":  config.LOOP_INTERVAL,
    "cycle_count":   0,
    "scan_secs":     None,
    "avg_scan_secs": None,
    "perf_warning":  False,
    "news_block":    "",
    "target_rr":     config.TARGET_RR,
    "default_lot":   config.DEFAULT_LOT,
    "occupied_symbols": [],
}
_scan_times = []
state_lock = threading.Lock()
_last_broker_ts: float = 0.0   # updated every scan with broker candle time
def _broker_now_utc():
    return (datetime.fromtimestamp(_last_broker_ts, tz=timezone.utc)
            if _last_broker_ts > 0 else datetime.now(timezone.utc))

STATS_FILE    = os.path.join(os.path.dirname(__file__), "session_stats.json")


def _load_session_stats() -> dict:
    """Load today's session stats from disk. Resets to 0 if the saved date is not today."""
    today = datetime.now(timezone.utc).date()
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "r") as f:
                data = json.load(f)
            if data.get("last_reset_date") == str(today):
                return {
                    "trades_today":       int(data.get("trades_today", 0)),
                    "consecutive_losses": int(data.get("consecutive_losses", 0)),
                    "last_reset_date":    today,
                }
    except Exception:
        pass
    return {"trades_today": 0, "consecutive_losses": 0, "last_reset_date": today}


def _save_session_stats() -> None:
    """Persist session stats to disk so they survive restarts within the same day."""
    try:
        with open(STATS_FILE, "w") as f:
            json.dump({
                "trades_today":       session_stats["trades_today"],
                "consecutive_losses": session_stats["consecutive_losses"],
                "last_reset_date":    str(session_stats["last_reset_date"]),
            }, f)
    except Exception:
        pass


session_stats = _load_session_stats()
stats_lock    = threading.Lock()

signal_memory = SignalMemory()

# ── Trade Journal (persistent JSON) ──────────────────────────────────────────
JOURNAL_FILE  = os.path.join(os.path.dirname(__file__), "journal.json")
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")
journal_lock  = threading.Lock()

# Latest mid-price per symbol — updated each scan cycle for SIM outcome detection
symbol_prices: dict[str, float] = {}
prices_lock   = threading.Lock()


def _load_journal() -> list:
    try:
        if os.path.exists(JOURNAL_FILE):
            with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_journal(entries: list) -> None:
    try:
        with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        print(f"[JOURNAL] Save error: {e}")


def _add_to_journal(decision: dict, lot: float, mode: str, reference_ts: float | None = None) -> None:
    """Auto-add a fired signal to the persistent trade journal."""
    symbol    = decision.get("symbol", config.SYMBOL)
    cfg       = config.get_symbol_cfg(symbol)
    pip_size  = cfg["pip_size"]

    entry_price = float(decision.get("entry", 0) or 0)
    sl_price    = float(decision.get("sl",    0) or 0)
    tp_price    = float(decision.get("tp",    0) or 0)

    sl_pips = round(abs(entry_price - sl_price) / pip_size, 1) if pip_size else 0
    tp_pips = round(abs(tp_price - entry_price)  / pip_size, 1) if pip_size else 0
    rr      = round(tp_pips / sl_pips, 2) if sl_pips else 0

    # BUG 4 FIX: compute pip_value dynamically from entry price
    # For JPY pairs (pip_size = 0.01): pip value floats with the exchange rate.
    #   pip_value = (pip_size / price) * 100_000  (USD per standard lot per pip)
    # For non-JPY USD-quoted pairs (pip_size = 0.0001): pip value is fixed $10.
    #   pip_value = pip_size * 100_000 = $10.00
    # Using the hardcoded config constant would drift by 3-11% as rates move.
    _USD_QUOTE_PAIRS = {"EUR/USD", "GBP/USD", "AUD/USD"}
    _symbol = decision.get("symbol", "")
    if entry_price > 0 and _symbol not in _USD_QUOTE_PAIRS:
        pip_value = (pip_size / entry_price) * 100_000
    else:
        pip_value = pip_size * 100_000

    # P&L at each outcome (for lot size used)
    pnl_win  = round(tp_pips * lot * pip_value, 2)
    pnl_loss = round(-sl_pips * lot * pip_value, 2)

    now = (datetime.fromtimestamp(reference_ts, tz=timezone.utc) if reference_ts else datetime.now(timezone.utc))
    entry = {
        "id":          str(uuid.uuid4())[:8],
        "timestamp":   now.strftime("%Y-%m-%d %H:%M UTC"),
        "date":        now.strftime("%Y-%m-%d"),
        "mode":        mode,
        "symbol":      symbol,
        "direction":   decision.get("type", ""),
        "strategy":    decision.get("strategy", ""),
        "confidence":  decision.get("confidence", 0),
        "entry":       entry_price,
        "sl":          sl_price,
        "tp":          tp_price,
        "sl_pips":     sl_pips,
        "tp_pips":     tp_pips,
        "rr":          rr,
        "lot":         lot,
        "pnl_win":     pnl_win,
        "pnl_loss":    pnl_loss,
        "result":      None,
        "pnl":         None,
        "reason":      decision.get("reason", ""),
        "auto_monitor": True,
    }
    with journal_lock:
        entries = _load_journal()
        entries.insert(0, entry)
        _save_journal(entries)


def _load_settings() -> None:
    """Load saved risk settings from disk and apply to config on startup."""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            if "target_rr"               in s: config.TARGET_RR               = float(s["target_rr"])
            if "default_lot"             in s: config.DEFAULT_LOT             = float(s["default_lot"])
            if "min_sl_pips"             in s: config.MIN_SL_PIPS             = int(s["min_sl_pips"])
            if "sl_buffer_pips"          in s: config.SL_BUFFER_PIPS          = int(s["sl_buffer_pips"])
            if "sweep_sl_buffer_pips"    in s: config.SWEEP_SL_BUFFER_PIPS    = int(s["sweep_sl_buffer_pips"])
            if "min_sweep_recovery_pips" in s: config.MIN_SWEEP_RECOVERY_PIPS = int(s["min_sweep_recovery_pips"])
            if "net_min_rr"              in s: config.NET_MIN_RR              = float(s["net_min_rr"])
            if "min_confidence"          in s: config.MIN_CONFIDENCE          = int(s["min_confidence"])
            if "max_trades_per_day"      in s: config.MAX_TRADES_PER_DAY      = int(s["max_trades_per_day"])
            print(f"[SETTINGS] Restored saved settings from {SETTINGS_FILE}")
            with state_lock:
                engine_state["default_lot"] = config.DEFAULT_LOT
                engine_state["target_rr"]   = config.TARGET_RR
    except Exception as e:
        print(f"[SETTINGS] Could not load settings: {e}")


def _save_settings() -> None:
    """Persist current risk settings to disk so they survive restarts."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "target_rr":               config.TARGET_RR,
                "default_lot":             config.DEFAULT_LOT,
                "min_sl_pips":             config.MIN_SL_PIPS,
                "sl_buffer_pips":          config.SL_BUFFER_PIPS,
                "sweep_sl_buffer_pips":    config.SWEEP_SL_BUFFER_PIPS,
                "min_sweep_recovery_pips": config.MIN_SWEEP_RECOVERY_PIPS,
                "net_min_rr":              config.NET_MIN_RR,
                "min_confidence":          config.MIN_CONFIDENCE,
                "max_trades_per_day":      config.MAX_TRADES_PER_DAY,
            }, f, indent=2)
    except Exception as e:
        print(f"[SETTINGS] Save error: {e}")


def _reset_daily_stats_if_needed():
    today = datetime.now(timezone.utc).date()
    with stats_lock:
        if session_stats["last_reset_date"] != today:
            session_stats["trades_today"]       = 0
            session_stats["consecutive_losses"] = 0
            session_stats["last_reset_date"]    = today
            signal_memory.clear()
            _save_session_stats()


def _score_all_strategies(state: dict) -> list:
    """Run all strategies and return their scores regardless of threshold."""
    scores = []
    for name, strategy_fn in STRATEGIES:
        try:
            result = strategy_fn(state, debug=False)
            if result:
                scores.append({
                    "name":      result.get("strategy", name),
                    "score":     result.get("confidence", 0),
                    "fired":     result.get("trade", False),
                    "reason":    result.get("reason", ""),
                    "direction": result.get("type", ""),
                    "_result":   result,
                })
            else:
                scores.append({
                    "name":      name,
                    "score":     0,
                    "fired":     False,
                    "reason":    "Conditions not met",
                    "direction": "",
                    "_result":   None,
                })
        except Exception as ex:
            scores.append({
                "name":      name,
                "score":     0,
                "fired":     False,
                "reason":    f"Error: {ex}",
                "direction": "",
                "_result":   None,
            })
    return scores


def _scan_symbol(sym: str) -> tuple[dict | None, list, dict | None]:
    """Scan one symbol. Returns (market_state, strategy_scores, best_decision)."""
    with controls_lock:
        ctrl       = symbol_controls.get(sym, {"enabled": True, "force_fire": False})
        enabled    = ctrl["enabled"]
        force_fire = ctrl["force_fire"]
        if force_fire:
            symbol_controls[sym]["force_fire"] = False

    if not enabled:
        return None, [], None

    # ── Per-symbol news filter (BoE → GBP/USD only, ECB → EUR/USD only) ──
    sym_blocked, sym_news_reason = is_symbol_blocked(sym,reference_ts=_last_broker_ts or None)
    if sym_blocked:
        print(f"[NEWS] ⏸  {sym} skipped: {sym_news_reason}")
        return None, [], None
    news_penalty = get_pair_confidence_penalty(sym, reference_ts=_last_broker_ts or None)

    cfg = config.get_symbol_cfg(sym)

    market_state = build_state(sym)
    if market_state is None:
        return None, [], None

    strategy_scores = _score_all_strategies(market_state)

    _sessions  = market_state.get("sessions", [])
    asian_only = "asian" in _sessions and "london" not in _sessions and "ny" not in _sessions

    if force_fire:
        signals = sorted(
            [s for s in strategy_scores if s["fired"]],
            key=lambda s: s["score"], reverse=True
        )
        if not signals:
            signals = sorted(strategy_scores, key=lambda s: s["score"], reverse=True)
    else:
        S6_NAME = "Asian Range Boundary Reaction"
        
        if news_penalty > 0:
            print(f"  [NEWS-PENALTY] {sym}  penalty={news_penalty}  threshold={config.MIN_CONFIDENCE + news_penalty}")
        signals = [
            s for s in strategy_scores
            if s["fired"] and s["score"] >= config.MIN_CONFIDENCE + news_penalty
            and (not asian_only or s.get("name") == S6_NAME)
        ]
        signals.sort(key=lambda s: s["score"], reverse=True)

    if len(signals) >= 2:
        top_dir    = signals[0].get("direction", "")
        second_dir = signals[1].get("direction", "")
        if top_dir and second_dir and top_dir != second_dir:
            print(
                f"  [ENGINE] ⚠️  DIRECTION CONFLICT: "
                f"{signals[0]['name']}={top_dir}({signals[0]['score']}) "
                f"vs {signals[1]['name']}={second_dir}({signals[1]['score']}) "
                f"— keeping {signals[0]['name']} only"
            )
            signals = [s for s in signals if s.get("direction", "") == top_dir]

    decision = None
    if signals:
        best   = signals[0]
        cached = best.get("_result")
        if cached and cached.get("trade"):
            decision               = cached
            decision["symbol"]     = sym
            decision["force_fire"] = force_fire

        # BUG 2 FIX: force-fire fallback — derive direction from market bias.
        # Previously hardcoded "BUY" regardless of market direction.
        # Now reads 4H bias first, falls back to 1H, defaults to BUY only
        # when both timeframes are neutral.
        if force_fire and decision is None:
            price = market_state.get("current_price")
            pip   = cfg["pip_size"]
            if price:
                bias = market_state.get("bias", {})
                b4h  = bias.get("4h", "neutral")
                b1h  = bias.get("1h", "neutral")
                if b4h == "bearish" or (b4h not in ("bullish", "bearish") and b1h == "bearish"):
                    ff_direction = "SELL"
                    sl = round(price + 15 * pip, 5)
                    tp = round(price - 30 * pip, 5)
                else:
                    ff_direction = "BUY"
                    sl = round(price - 15 * pip, 5)
                    tp = round(price + 30 * pip, 5)
                decision = {
                    "trade":      True,
                    "type":       ff_direction,
                    "symbol":     sym,
                    "confidence": best["score"],
                    "strategy":   best["name"],
                    "reason":     f"FORCE-FIRE — score {best['score']}",
                    "entry":      price,
                    "sl":         sl,
                    "tp":         tp,
                    "rr":         2.0,
                    "force_fire": True,
                }

    return market_state, strategy_scores, decision


def run_engine_cycle():
    global _last_broker_ts 
    _reset_daily_stats_if_needed()
    with state_lock:
        engine_state["sessions"] = get_active_sessions(reference_ts=_last_broker_ts if _last_broker_ts > 0 
    else None
    )
     

    # ── News filter — global block (Fed, NFP, daily windows) stops all pairs ─
    globally_blocked, news_reason = is_global_blocked(reference_ts=_last_broker_ts or None)
    if globally_blocked:
        print(f"[NEWS] ⏸  All pairs blocked: {news_reason}")
        with state_lock:
            engine_state["status"]      = "news_block"
            engine_state["news_block"]  = news_reason
            engine_state["last_update"] = _broker_now_utc().strftime("%H:%M:%S UTC")
        return
    else:
        with state_lock:
            engine_state["news_block"] = ""
    # BoE/ECB per-symbol filtering is applied inside _scan_symbol() below.
    # ─────────────────────────────────────────────────────────────────────

    with state_lock:
        engine_state["status"] = "scanning"

    

    scan_symbols = config.SCAN_SYMBOLS
    print(f"\n[ENGINE] Scanning {len(scan_symbols)} symbols: {', '.join(scan_symbols)}")

    best_decision    = None
    best_score       = 0
    best_state       = None
    best_sym         = config.SYMBOL
    all_scores       = []

    def _scan_with_sym(sym):
          
        return sym, _scan_symbol(sym)

    with ThreadPoolExecutor(max_workers=len(scan_symbols)) as executor:
        parallel_results = list(executor.map(_scan_with_sym, scan_symbols))
         # ── Update occupied symbols for dashboard display ──────────────────────

    if MT5_AVAILABLE and not config.SIMULATION_MODE and not SIM_FORCE:
        occupied = [sym for sym in scan_symbols if has_open_position(sym)]
        with state_lock:
            engine_state["occupied_symbols"] = occupied
          

    for sym, (market_state, scores, decision) in parallel_results:
        if market_state is None:
            continue

        if best_state is None or sym == config.SYMBOL:
            best_state = market_state
            best_sym   = sym

        for s in scores:
            s["symbol"] = sym
        all_scores.extend(scores)

        if decision and decision.get("confidence", 0) > best_score:
            best_score    = decision["confidence"]
            best_decision = decision
            best_state    = market_state
            best_sym      = sym

    if best_state is None:
        with state_lock:
            engine_state["status"] = "error"
            engine_state["last_update"] = _broker_now_utc().strftime("%H:%M:%S UTC")
        return
    
    
    _last_broker_ts = best_state.get("reference_ts", _last_broker_ts)



    approved_decision = None
    block_reason      = None

    if best_decision:
        if not config.SIMULATION_MODE and not SIM_FORCE and MT5_AVAILABLE:
            _sym_to_check = best_decision.get("symbol", config.SYMBOL)
            if live_order.__module__ and has_open_position(_sym_to_check):
                print(f"  [GUARD] {_sym_to_check} already has an open position — skipping signal")
                best_decision = None
                block_reason  = f"Open position on {_sym_to_check} — no hedge"
        if best_decision and signal_memory.is_duplicate(best_decision, best_state):
            block_reason = "Duplicate setup — same signal already traded this structure"
        elif best_decision:
            approved, reason = validate(best_decision, session_stats)
            if approved:
                approved_decision = best_decision
            else:
                block_reason = reason

    if approved_decision:
        cfg = config.get_symbol_cfg(approved_decision.get("symbol", config.SYMBOL))
        config.PIP_SIZE   = cfg["pip_size"]
        config.MT5_SYMBOL = cfg["mt5_name"]

        use_sim    = config.SIMULATION_MODE or SIM_FORCE or not MT5_AVAILABLE
        lot        = get_lot_size()
        success    = sim_order(approved_decision, lot) if use_sim else live_order(approved_decision, lot)
        mode_label = "SIM" if use_sim else "LIVE"
        log_trade(approved_decision, executed=success, mode=mode_label)
        
        if success and approved_decision.get("strategy") == "Asian Range Boundary Reaction":
                from strategies.scalp6 import _mark_fired as _s6_mark_fired
                _s6_mark_fired(
                approved_decision.get("symbol", ""),approved_decision.get("boundary_side", "")
            )


        if success:
            with stats_lock:
                session_stats["trades_today"] += 1
                _save_session_stats()
            signal_memory.record(approved_decision, best_state)
            
            _last_broker_ts = best_state.get("reference_ts") or _last_broker_ts
            _add_to_journal(approved_decision, lot, mode_label, reference_ts=best_state.get("reference_ts"))
            trade_entry = {
                "time":       _broker_now_utc().strftime("%H:%M UTC"),
                "mode":       mode_label,
                "symbol":     approved_decision.get("symbol", best_sym),
                "strategy":   approved_decision.get("strategy", ""),
                "direction":  approved_decision.get("type", ""),
                "entry":      approved_decision.get("entry", 0),
                "sl":         approved_decision.get("sl", 0),
                "tp":         approved_decision.get("tp", 0),
                "confidence": approved_decision.get("confidence", 0),
                "reason":     approved_decision.get("reason", ""),
            }
            with state_lock:
                engine_state["trade_log"].insert(0, trade_entry)
                if len(engine_state["trade_log"]) > 50:
                    engine_state["trade_log"] = engine_state["trade_log"][:50]

    bias = best_state.get("bias", {})
    asia = best_state.get("asia_range", {})
    m5   = best_state.get("5m", {})
    m15  = best_state.get("15m", {})

    with state_lock:
        engine_state["status"]             = "running"
        engine_state["symbol"]             = best_sym
        engine_state["price"]              = best_state.get("current_price")
        engine_state["bias"]               = bias
        engine_state["sessions"]           = best_state.get("sessions", [])
        engine_state["asia_range"]         = asia
        engine_state["bos_count"]          = len(m5.get("bos", []))
        engine_state["choch_count"]        = len(m15.get("choch", []))
        engine_state["zone_count"]         = len(m5.get("zones", []))
        engine_state["strategy_scores"]    = all_scores
        engine_state["active_signal"]      = approved_decision
        engine_state["scan_symbols"]       = scan_symbols
        engine_state["trades_today"]       = session_stats["trades_today"]
        engine_state["consecutive_losses"] = session_stats["consecutive_losses"]
        engine_state["last_update"]        = _broker_now_utc().strftime("%H:%M:%S UTC")
        engine_state["cycle_count"]       += 1
        if block_reason:
            engine_state["last_block"] = block_reason

    with prices_lock:
        for _sym, (_mstate, _, _) in parallel_results:
            if _mstate is not None:
                _p = _mstate.get("current_price")
                if _p:
                    symbol_prices[_sym] = _p


def engine_loop():
    """Background thread — runs engine cycle every LOOP_INTERVAL seconds."""
    time.sleep(2)
    while True:
        try:
            _t0 = time.time()
            run_engine_cycle()
            _dur = round(time.time() - _t0, 1)
            _scan_times.append(_dur)
            if len(_scan_times) > 5:
                _scan_times.pop(0)
            _avg = round(sum(_scan_times) / len(_scan_times), 1) 
            with state_lock:
                engine_state["scan_secs"]     = _dur
                engine_state["avg_scan_secs"] = _avg
                engine_state["perf_warning"]  = _avg > 25
        except Exception as e:
            with state_lock:
                engine_state["status"] = "error"
            print(f"  [ENGINE ERROR] {e}")

        for i in range(config.LOOP_INTERVAL, 0, -1):
            with state_lock:
                engine_state["next_scan_in"] = i
            time.sleep(1)


# ── Auto outcome watcher ──────────────────────────────────────────────────────
def _auto_mark_result(tid: str, result: str) -> None:
    """Mark a journal entry W or L internally — mirrors set_journal_result logic."""
    with journal_lock:
        entries = _load_journal()
        for e in entries:
            if e.get("id") == tid and e.get("result") is None:
                e["result"]       = result
                e["pnl"]          = e["pnl_win"] if result == "W" else e["pnl_loss"]
                e["auto_monitor"] = False
                _save_journal(entries)
                trade_date = e.get("date", "")
                today_str  = _broker_now_utc().strftime("%Y-%m-%d")
                if trade_date == today_str:
                    with stats_lock:
                        if result == "L":
                           session_stats["consecutive_losses"] += 1
                        else:        
                           session_stats["consecutive_losses"] = 0
                        _save_session_stats()


def _outcome_watcher() -> None:
    """Background thread: polls price levels (SIM) or MT5 history (LIVE)
    and auto-marks pending journal entries W or L."""
    while True:
        time.sleep(config.LOOP_INTERVAL)
        try:
            with journal_lock:
                entries = _load_journal()
            pending = [e for e in entries
                       if e.get("result") is None and e.get("auto_monitor")]
            if not pending:
                continue
            now_utc = datetime.now(timezone.utc)
            for entry in pending:
                sym       = entry.get("symbol", "")
                direction = entry.get("direction", "BUY")
                sl        = float(entry.get("sl",  0) or 0)
                tp        = float(entry.get("tp",  0) or 0)
                mode      = entry.get("mode", "SIM")
                tid       = entry["id"]
                if sl == 0 or tp == 0:
                    continue
                if mode == "SIM":
                    with prices_lock:
                        price = symbol_prices.get(sym)
                    if price is None:
                        continue
                    if direction == "BUY":
                        if price >= tp:
                            _auto_mark_result(tid, "W")
                        elif price <= sl:
                            _auto_mark_result(tid, "L")
                    else:
                        if price <= tp:
                            _auto_mark_result(tid, "W")
                        elif price >= sl:
                            _auto_mark_result(tid, "L")
                elif mode == "LIVE":
                    try:
                        import MetaTrader5 as mt5
                        if not mt5.initialize():
                            continue
                        sym_cfg    = config.get_symbol_cfg(sym)
                        mt5_symbol = sym_cfg["mt5_name"]
                        try:
                            from_dt = datetime.strptime(
                                entry.get("timestamp", ""),
                                "%Y-%m-%d %H:%M UTC"
                            ).replace(tzinfo=timezone.utc)
                        except ValueError:
                            mt5.shutdown()
                            continue
                        deals = mt5.history_deals_get(from_dt, now_utc,
                                                      group=f"*{mt5_symbol}*")
                        if deals:
                            for deal in deals:
                                if getattr(deal, "entry", -1) == 1:
                                    result = "W" if deal.profit >= 0 else "L"
                                    _auto_mark_result(tid, result)
                                    break
                        mt5.shutdown()
                    except Exception:
                        pass
        except Exception:
            pass


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route('/api/heatmap')
def heatmap():
    # BUG 5 FIX: added S6 "Asian Range Boundary Reaction" to short-name map
    short = {
        "MTF Pullback Precision Scalping":   "S1 Pullback",
        "Liquidity Sweep Reversal Scalping":  "S2 Sweep",
        "ICT OB/FVG Zone Reaction":           "S3 OB/FVG",
        "Volatility Compression Breakout":    "S4 Compression",
        "Session Open Momentum Scalp":        "S5 Session",
        "Asian Range Boundary Reaction":      "S6 Asian",
    }
    result = {}
    for sym in config.SCAN_SYMBOLS:
        result[sym] = {}
        try:
            state = build_state(sym)
        except Exception:
            for label in short.values():
                result[sym][label] = {"score": 0, "direction": None, "error": True}
            continue
        for full_name, strategy_fn in STRATEGIES:
            label = short.get(full_name, full_name)
            try:
                decision = strategy_fn(state)
                if decision:
                    result[sym][label] = {
                        "score":     decision.get("confidence", 0),
                        "direction": decision.get("type"),
                        "error":     False,
                    }
                else:
                    result[sym][label] = {"score": 0, "direction": None, "error": False}
            except Exception:
                result[sym][label] = {"score": 0, "direction": None, "error": False}
    import time as _t
    return jsonify({"heatmap": result, "timestamp": _t.time()})


@app.route("/api/status")
def api_status():
    with state_lock:
        return jsonify(engine_state)


@app.route("/api/mode/<mode>", methods=["POST"])
def set_mode(mode):
    if mode in ("sim", "live"):
        with state_lock:
            engine_state["mode"] = "SIMULATION" if mode == "sim" else "LIVE"
        config.SIMULATION_MODE = (mode == "sim")
    return jsonify({"ok": True, "mode": engine_state["mode"]})


@app.route("/api/settings", methods=["GET"])
def get_settings():
    """Return all current runtime settings so the UI can populate the settings panel."""
    return jsonify({
        "target_rr":               config.TARGET_RR,
        "default_lot":             config.DEFAULT_LOT,
        "min_sl_pips":             config.MIN_SL_PIPS,
        "sl_buffer_pips":          config.SL_BUFFER_PIPS,
        "sweep_sl_buffer_pips":    config.SWEEP_SL_BUFFER_PIPS,
        "min_sweep_recovery_pips": config.MIN_SWEEP_RECOVERY_PIPS,
        "net_min_rr":              config.NET_MIN_RR,
        "min_confidence":          config.MIN_CONFIDENCE,
        "max_trades_per_day":      config.MAX_TRADES_PER_DAY,
    })


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.get_json(force=True, silent=True) or {}

    VALID_RR  = [2.0, 3.0, 5.0]
    VALID_LOT = [0.01, 0.02, 0.05]

    rr  = data.get("target_rr")
    lot = data.get("default_lot")
    if rr is not None and float(rr) in VALID_RR:
        config.TARGET_RR = float(rr)
        with state_lock:
            engine_state["target_rr"] = config.TARGET_RR

    if lot is not None and float(lot) in VALID_LOT:
        config.DEFAULT_LOT = float(lot)
        with state_lock:
            engine_state["default_lot"] = config.DEFAULT_LOT

    def _clamp(val, lo, hi):
        return max(lo, min(hi, val))

    if "min_sl_pips" in data:
        config.MIN_SL_PIPS = int(_clamp(int(data["min_sl_pips"]), 5, 30))

    if "sl_buffer_pips" in data:
        config.SL_BUFFER_PIPS = int(_clamp(int(data["sl_buffer_pips"]), 2, 15))

    if "sweep_sl_buffer_pips" in data:
        config.SWEEP_SL_BUFFER_PIPS = int(_clamp(int(data["sweep_sl_buffer_pips"]), 3, 20))

    if "min_sweep_recovery_pips" in data:
        config.MIN_SWEEP_RECOVERY_PIPS = int(_clamp(int(data["min_sweep_recovery_pips"]), 1, 10))

    if "net_min_rr" in data:
        config.NET_MIN_RR = round(_clamp(float(data["net_min_rr"]), 1.0, 3.0), 1)

    if "min_confidence" in data:
        config.MIN_CONFIDENCE = int(_clamp(int(data["min_confidence"]), 50, 100))

    if "max_trades_per_day" in data:
        config.MAX_TRADES_PER_DAY = int(_clamp(int(data["max_trades_per_day"]), 1, 10))

    _save_settings()
    return jsonify({
        "ok":                      True,
        "target_rr":               config.TARGET_RR,
        "default_lot":             config.DEFAULT_LOT,
        "min_sl_pips":             config.MIN_SL_PIPS,
        "sl_buffer_pips":          config.SL_BUFFER_PIPS,
        "sweep_sl_buffer_pips":    config.SWEEP_SL_BUFFER_PIPS,
        "min_sweep_recovery_pips": config.MIN_SWEEP_RECOVERY_PIPS,
        "net_min_rr":              config.NET_MIN_RR,
        "min_confidence":          config.MIN_CONFIDENCE,
        "max_trades_per_day":      config.MAX_TRADES_PER_DAY,
    })


@app.route("/api/symbol/<symbol_key>", methods=["POST"])
def set_symbol(symbol_key: str):
    """Switch the active trading symbol at runtime."""
    symbol = symbol_key.replace("-", "/").upper()
    cfg = config.get_symbol_cfg(symbol)
    if cfg == config.get_symbol_cfg("USD/JPY") and symbol != "USD/JPY":
        return jsonify({"ok": False, "error": f"Unknown symbol {symbol}"}), 400
    config.SYMBOL     = symbol
    config.PIP_SIZE   = cfg["pip_size"]
    config.MT5_SYMBOL = cfg["mt5_name"]
    with state_lock:
        engine_state["symbol"] = symbol
        engine_state["price"]  = None
    return jsonify({"ok": True, "symbol": symbol, "pip_size": cfg["pip_size"], "mt5_name": cfg["mt5_name"]})


@app.route("/api/symbols", methods=["GET"])
def get_symbols():
    """Return all symbols with their enabled/force-fire state and last score."""
    with controls_lock:
        ctrl_snapshot = {k: dict(v) for k, v in symbol_controls.items()}
    with state_lock:
        scores_snapshot = engine_state.get("strategy_scores", [])
    last_scores: dict[str, int] = {}
    for s in scores_snapshot:
        sym = s.get("symbol", "")
        sc  = s.get("score", 0)
        if sc > last_scores.get(sym, -1):
            last_scores[sym] = sc
    result = {}
    for sym in config.SCAN_SYMBOLS:
        result[sym] = {
            "enabled":    ctrl_snapshot.get(sym, {}).get("enabled", True),
            "force_fire": ctrl_snapshot.get(sym, {}).get("force_fire", False),
            "last_score": last_scores.get(sym, 0),
        }
    return jsonify(result)


@app.route("/api/symbols/toggle", methods=["POST"])
def toggle_symbol():
    """Enable or disable a symbol. Body: {"symbol": "USD/JPY", "enabled": true}"""
    data    = request.get_json(force=True, silent=True) or {}
    sym     = data.get("symbol", "").upper().replace("-", "/")
    enabled = data.get("enabled")
    if sym not in config.SCAN_SYMBOLS or enabled is None:
        return jsonify({"ok": False, "error": "Unknown symbol or missing 'enabled'"}), 400
    with controls_lock:
        symbol_controls[sym]["enabled"] = bool(enabled)
    return jsonify({"ok": True, "symbol": sym, "enabled": bool(enabled)})


@app.route("/api/symbols/force-fire", methods=["POST"])
def force_fire_symbol():
    """Queue a force-fire for one symbol on the next scan cycle.
    Body: {"symbol": "USD/JPY"}"""
    data = request.get_json(force=True, silent=True) or {}
    sym  = data.get("symbol", "").upper().replace("-", "/")
    if sym not in config.SCAN_SYMBOLS:
        return jsonify({"ok": False, "error": f"Unknown symbol: {sym}"}), 400
    with controls_lock:
        symbol_controls[sym]["enabled"]    = True
        symbol_controls[sym]["force_fire"] = True
    return jsonify({"ok": True, "symbol": sym, "message": f"Force-fire queued for {sym} — fires on next scan cycle"})


@app.route("/api/journal", methods=["GET"])
def get_journal():
    """Return all journal entries with computed summary stats."""
    with journal_lock:
        entries = _load_journal()

    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    marked  = [e for e in entries if e.get("result") in ("W", "L")]
    wins    = [e for e in marked if e["result"] == "W"]
    losses  = [e for e in marked if e["result"] == "L"]

    total_pnl = sum(e.get("pnl", 0) or 0 for e in marked)
    today_pnl = sum(e.get("pnl", 0) or 0 for e in marked if e.get("date") == today)
    win_rate  = round(len(wins) / len(marked) * 100, 1) if marked else 0
    avg_rr    = round(sum(e.get("rr", 0) for e in entries) / len(entries), 2) if entries else 0

    chrono  = list(reversed(marked))
    equity  = []
    running = 0.0
    for e in chrono:
        running += e.get("pnl", 0) or 0
        equity.append({"date": e["timestamp"], "pnl": round(running, 2)})

    day_map: dict = {}
    for e in marked:
        d = e.get("date", "")
        if d not in day_map:
            day_map[d] = {"wins": 0, "losses": 0, "pnl": 0.0}
        day_map[d]["pnl"]    += e.get("pnl", 0) or 0
        day_map[d]["wins"]   += 1 if e["result"] == "W" else 0
        day_map[d]["losses"] += 1 if e["result"] == "L" else 0
    daily = [{"date": k, **v, "pnl": round(v["pnl"], 2)} for k, v in sorted(day_map.items(), reverse=True)]

    return jsonify({
        "entries": entries,
        "stats": {
            "total_trades": len(entries),
            "marked":       len(marked),
            "wins":         len(wins),
            "losses":       len(losses),
            "win_rate":     win_rate,
            "total_pnl":    round(total_pnl, 2),
            "today_pnl":    round(today_pnl, 2),
            "avg_rr":       avg_rr,
        },
        "equity_curve": equity,
        "daily":        daily,
    })


@app.route("/api/journal/result", methods=["POST"])
def set_journal_result():
    """Mark a trade W or L. Body: {"id": "abc123", "result": "W"}"""
    data   = request.get_json(force=True, silent=True) or {}
    tid    = data.get("id", "")
    result = data.get("result", "").upper()
    if result not in ("W", "L"):
        return jsonify({"ok": False, "error": "result must be W or L"}), 400

    with journal_lock:
        entries = _load_journal()
        matched = False
        for e in entries:
            if e.get("id") == tid:
                # BUG 3 FIX: capture existing result before overwriting.
                # Counter only adjusts when the result actually changes,
                # so re-marking the same outcome twice is a no-op.
                prev_result       = e.get("result")
                e["result"]       = result
                e["pnl"]          = e["pnl_win"] if result == "W" else e["pnl_loss"]
                e["auto_monitor"] = False
                matched = True
                if prev_result != result:
                    trade_date = e.get("date", "")
                    today_str  = _broker_now_utc().strftime("%Y-%m-%d")
                    if trade_date == today_str:
                        with stats_lock:
                            if result == "L":
                                session_stats["consecutive_losses"] += 1
                            elif result == "W":
                                session_stats["consecutive_losses"] = 0
                            _save_session_stats()                     
                        
                break
        if not matched:
            return jsonify({"ok": False, "error": f"Trade {tid} not found"}), 404
        _save_journal(entries)
    return jsonify({"ok": True, "id": tid, "result": result})


@app.route("/api/journal/unmark", methods=["POST"])
def unmark_journal_result():
    """Remove a W/L result, resetting a trade to pending. Body: {"id": "abc123"}"""
    data = request.get_json(force=True, silent=True) or {}
    tid  = data.get("id", "")
    with journal_lock:
        entries = _load_journal()
        matched = False
        for e in entries:
            if e.get("id") == tid:
                e["result"]       = None
                e["pnl"]          = None
                e["auto_monitor"] = True
                matched = True
                break
        if not matched:
            return jsonify({"ok": False, "error": f"Trade {tid} not found"}), 404
        _save_journal(entries)
    return jsonify({"ok": True, "id": tid})


@app.route("/api/journal/clear", methods=["POST"])
def clear_journal():
    """Clear the entire journal. Irreversible."""
    with journal_lock:
        _save_journal([])
    return jsonify({"ok": True, "message": "Journal cleared"})


@app.route("/api/news/upcoming", methods=["GET"])
def api_news_upcoming():
    """
    Return upcoming blocked trading days within the next N days.
    Query param: ?days=30 (default 30, max 90)

    Each entry contains:
      date          — YYYY-MM-DD
      weekday       — Monday … Sunday
      event         — human-readable event name
      scope         — 'all_pairs' | 'gbp_pairs' | 'eur_pairs'
      pairs_blocked — comma-separated symbols affected, or 'ALL'
    """
    try:
        days = int(request.args.get("days", 30))
        days = max(1, min(days, 90))
    except (ValueError, TypeError):
        days = 30

    upcoming = get_upcoming_blocked_days(days=days)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_events = [e for e in upcoming if e["date"] == today_str]
    future_events = [e for e in upcoming if e["date"] > today_str]

    return jsonify({
        "window_days":    days,
        "today":          today_str,
        "today_blocked":  today_events,
        "upcoming":       future_events,
        "total_events":   len(upcoming),
        "note": (
            "Daily recurring windows (UK/EU 06:45–08:30, US 12:15–13:30, "
            "Fed speakers 13:30–14:30 UTC) apply every weekday and are not listed here."
        ),
    })


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    data = request.get_json(force=True, silent=True) or {}
    days = int(data.get("days", 7))
    days = max(7, min(days, 90))
    try:
        from backtester import run_backtest
        results = run_backtest(days=days)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    _load_settings()
    use_sim = config.SIMULATION_MODE or SIM_FORCE

    print("=" * 68)
    print("  STRUCT.ai Scalping Engine  —  Local Dashboard")
    print(f"  Mode      : {'SIMULATION' if use_sim else 'LIVE TRADING ⚠️'}")
    print(f"  Dashboard : http://localhost:{PORT}")
    print(f"  API       : {config.STRUCT_API_BASE}")
    print(f"  Scanning  : {len(config.SCAN_SYMBOLS)} symbols — {', '.join(config.SCAN_SYMBOLS)}")
    print(f"  Exec TF   :  5M")
    print(f"  Confirm TF: 15M")
    print(f"  Bias TF   : 1H / 4H")
    print("=" * 68)
    print()

    if not use_sim and LIVE_FORCE:
        print("  ⚠️  LIVE MODE — real orders will be placed on MT5!")
        print("  Starting in 5 seconds... press Ctrl+C to abort")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n  Aborted.")
            sys.exit(0)

    with state_lock:
        engine_state["mode"] = "SIMULATION" if use_sim else "LIVE"

    t = threading.Thread(target=engine_loop, daemon=True)
    t.start()

    watcher = threading.Thread(target=_outcome_watcher, daemon=True)
    watcher.start()

    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()