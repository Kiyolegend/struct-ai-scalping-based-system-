"""
State Builder — fetches all data from STRUCT.ai and builds a unified snapshot.

Scalping edition: fetches 5M (execution TF) + 15M (confirmation) + 1H/4H (bias).
Attempts to fetch 1M data if available; falls back gracefully if not.
"""

import requests
from datetime import datetime, timezone
import config
from config import STRUCT_API_BASE

TIMEOUT = 12


def _get(path: str, params: dict = None) -> dict | None:
    try:
        r = requests.get(f"{STRUCT_API_BASE}/{path}", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [WARN] STRUCT.ai /{path} failed: {e}")
        return None


def _analysis(interval: str, outputsize: int = 200, symbol: str = None) -> dict | None:
    return _get("analysis", {"symbol": symbol or config.SYMBOL, "interval": interval, "outputsize": outputsize})


def get_active_sessions() -> list[str]:
    """Returns list of currently active sessions based on UTC time."""
    hour = datetime.now(timezone.utc).hour
    sessions = []
    if 0 <= hour < 9:
        sessions.append("asian")
    if 8 <= hour < 17:
        sessions.append("london")
    if 13 <= hour < 22:
        sessions.append("ny")
    return sessions


def is_tradeable_session(sessions: list[str]) -> bool:
    return any(s in sessions for s in ["london", "ny"])


def _get_asia_range(candles: list) -> tuple[float | None, float | None]:
    """Extract today's Asian session high/low from 5m candles (UTC 00:00-09:00)."""
    if not candles:
        return None, None
    today = datetime.now(timezone.utc).date()
    asia = [
        c for c in candles
        if 0 <= datetime.fromtimestamp(c["time"], tz=timezone.utc).hour < 9
        and datetime.fromtimestamp(c["time"], tz=timezone.utc).date() == today
    ]
    if not asia:
        return None, None
    return max(c["high"] for c in asia), min(c["low"] for c in asia)


def sanitize_state(state: dict) -> dict | None:
    """
    Validate and normalise a state dict before it reaches any strategy.
    Returns None if the state is fundamentally unusable (missing price, etc.).
    Fills in safe defaults for any optional missing/null fields.
    """
    import math
    if not isinstance(state, dict):
        return None

    price = state.get("current_price")
    if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return None

    bias = state.get("bias") or {}
    state["bias"] = {
        "4h":  bias.get("4h")  or "neutral",
        "1h":  bias.get("1h")  or "neutral",
        "15m": bias.get("15m") or "neutral",
    }

    for tf in ("1m", "5m", "15m", "1h", "4h"):
        tf_data = state.get(tf) or {}
        state[tf] = {
            "trend":     tf_data.get("trend")     or "neutral",
            "structure": tf_data.get("structure") or [],
            "bos":       tf_data.get("bos")       or [],
            "choch":     tf_data.get("choch")      or [],
            "zones":     tf_data.get("zones") if isinstance(tf_data.get("zones"), list) else [],
            "candles":   tf_data.get("candles")    or [],
            "sr_levels": tf_data.get("sr_levels")  or [],
        }

    asia = state.get("asia_range") or {}
    state["asia_range"] = {
        "high": asia.get("high") if isinstance(asia.get("high"), (int, float)) else None,
        "low":  asia.get("low")  if isinstance(asia.get("low"),  (int, float)) else None,
    }

    if "sr_levels" not in state:
        state["sr_levels"] = []
    if "sessions" not in state:
        state["sessions"] = []
    if "tradeable_session" not in state:
        state["tradeable_session"] = False

    return state


def build_state(symbol: str = None) -> dict | None:
    """Fetch all STRUCT.ai endpoints and return a unified state object.

    Scalping TF priorities:
      1M  — execution / micro-entry (optional, falls back if unavailable)
      5M  — primary execution TF
      15M — confirmation
      1H  — bias
      4H  — macro bias
    """
    sym = symbol or config.SYMBOL
    print(f"  Fetching {sym} from STRUCT.ai...", end=" ", flush=True)

    bias = _get("mtf-bias", {"symbol": sym})
    a5m  = _analysis("5m",  300, sym)
    a15m = _analysis("15m", 150, sym)
    a1h  = _analysis("1h",  150, sym)
    a4h  = _analysis("4h",  100, sym)
    sr   = _get("sr-levels", {"symbol": sym, "outputsize": 300})

    if not all([bias, a5m, a15m, a1h, a4h, sr]):
        print("FAILED")
        return None

    candles_5m = a5m.get("candles", [])
    if not candles_5m:
        print("FAILED — no 5m candles")
        return None

    current_price = candles_5m[-1].get("close")
    if not isinstance(current_price, (int, float)) or current_price <= 0:
        print("FAILED — invalid or missing price from API")
        return None

    # 1M is optional — fetch best-effort
    a1m = _analysis("1m", 100)

    sessions      = get_active_sessions()
    asia_high, asia_low = _get_asia_range(candles_5m)

    bias_15m = bias.get("bias_15m", {}).get("trend") or "neutral"
    bias_1h  = bias.get("bias_1h",  {}).get("trend") or "neutral"
    bias_4h  = bias.get("bias_4h",  {}).get("trend") or "neutral"

    print(f"OK  [price={current_price:.3f}  sessions={sessions}  bias=4H:{bias_4h}/1H:{bias_1h}/15M:{bias_15m}]")

    candles_1m = (a1m or {}).get("candles", [])

    return sanitize_state({
        "symbol":         sym,
        "current_price":  current_price,
        "sessions":       sessions,
        "tradeable_session": is_tradeable_session(sessions),
        "bias": {
            "15m": bias_15m,
            "1h":  bias_1h,
            "4h":  bias_4h,
        },
        "1m": {
            "trend":     (a1m or {}).get("trend", {}).get("trend", "neutral"),
            "structure": (a1m or {}).get("structure_labels", []),
            "bos":       (a1m or {}).get("bos", []),
            "choch":     (a1m or {}).get("choch", []),
            "zones":     (a1m or {}).get("zones", []),
            "candles":   candles_1m,
        },
        "5m": {
            "trend":     a5m.get("trend", {}).get("trend", "neutral"),
            "structure": a5m.get("structure_labels", []),
            "bos":       a5m.get("bos", []),
            "choch":     a5m.get("choch", []),
            "zones":     a5m.get("zones", []),
            "candles":   candles_5m,
        },
        "15m": {
            "trend":     a15m.get("trend", {}).get("trend", "neutral"),
            "structure": a15m.get("structure_labels", []),
            "bos":       a15m.get("bos", []),
            "choch":     a15m.get("choch", []),
            "zones":     a15m.get("zones", []),
        },
        "1h": {
            "trend":     a1h.get("trend", {}).get("trend", "neutral"),
            "structure": a1h.get("structure_labels", []),
            "bos":       a1h.get("bos", []),
            "choch":     a1h.get("choch", []),
            "zones":     a1h.get("zones", []),
            "candles":   a1h.get("candles", []),
        },
        "4h": {
            "trend":     a4h.get("trend", {}).get("trend", "neutral"),
            "structure": a4h.get("structure_labels", []),
            "bos":       a4h.get("bos", []),
            "choch":     a4h.get("choch", []),
            "zones":     a4h.get("zones", []),
            "candles":   a4h.get("candles", []),
        },
        "sr_levels": sr.get("levels", []),
        "asia_range": {"high": asia_high, "low": asia_low},
    })