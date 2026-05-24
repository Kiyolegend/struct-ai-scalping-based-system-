"""
STRUCT.ai Scalping Engine — Live News Filter (Drop-in Replacement)
===================================================================
Drop this file into your scalping engine folder alongside the existing
news_filter.py. It replaces the static hardcoded calendar with a live
call to the News Impact Service, while keeping the hardcoded dates as
a fallback if the service is unreachable.

How to use:
  1. Start the News Impact Service (python news_impact_server.py)
  2. Copy this file into your scalping engine directory
  3. In dashboard_server.py, change:
       from news_filter import is_global_blocked, is_symbol_blocked, ...
     to:
       from news_filter_live import is_global_blocked, is_symbol_blocked, ...
  4. That's it — everything else stays the same.

Fallback hierarchy:
  1. Live service at NEWS_IMPACT_URL (default: http://localhost:5003)
  2. Hardcoded static dates from the original news_filter.py

The live service is tried first on every call. If it's unreachable (timeout
or connection refused), the static fallback takes over transparently.
"""

import os
import requests
from datetime import datetime, timezone, timedelta

# ── Live service configuration ────────────────────────────────────────────────
NEWS_IMPACT_URL = os.getenv("NEWS_IMPACT_URL", "http://localhost:5003")
SERVICE_TIMEOUT = 2          # seconds — fast timeout so the engine never stalls
_SERVICE_WARN_PRINTED = False  # suppress repeated connection error spam


def _get_pair_impact(pair: str) -> dict | None:
    """Call the live impact service for one pair. Returns None on any error."""
    global _SERVICE_WARN_PRINTED
    try:
        r = requests.get(
            f"{NEWS_IMPACT_URL}/api/impact/symbol",
            params={"pair": pair},
            timeout=SERVICE_TIMEOUT,
        )
        r.raise_for_status()
        _SERVICE_WARN_PRINTED = False   # reset warning state if service recovers
        return r.json()
    except Exception as e:
        if not _SERVICE_WARN_PRINTED:
            print(f"  [NEWS-LIVE] Service unreachable ({e}) — falling back to static calendar")
            _SERVICE_WARN_PRINTED = True
        return None


# ── Static fallback (from original news_filter.py) ───────────────────────────
# Keep this in sync with the main news_filter.py if you add new dates there.

GBP_PAIRS = {"GBP/USD"}
EUR_PAIRS = {"EUR/USD"}

DAILY_BLOCKED_WINDOWS = [
    (6,  45,  8, 30, "UK/EU data window (CPI, GDP, PMI, employment)"),
    (12, 15, 13, 30, "US data window (CPI, NFP, retail sales, GDP)"),
    (13, 30, 14, 30, "Fed speaker / FOMC window"),
]

FED_DATES = {
    (2025, 1, 29), (2025, 3, 19), (2025, 5, 7),  (2025, 6, 18),
    (2025, 7, 30), (2025, 9, 17), (2025, 11, 5), (2025, 12, 17),
    (2026, 1, 28), (2026, 3, 18), (2026, 5, 6),  (2026, 6, 17),
    (2026, 7, 29), (2026, 9, 16), (2026, 11, 4), (2026, 12, 16),
    (2027, 1, 27), (2027, 3, 17), (2027, 5, 5),  (2027, 6, 16),
    (2027, 7, 28), (2027, 9, 15), (2027, 11, 3), (2027, 12, 15),
}

BOE_DATES = {
    (2025, 2, 6),  (2025, 3, 20), (2025, 5, 8),  (2025, 6, 19),
    (2025, 8, 7),  (2025, 9, 18), (2025, 11, 6), (2025, 12, 18),
    (2026, 2, 5),  (2026, 3, 19), (2026, 5, 7),  (2026, 6, 18),
    (2026, 8, 6),  (2026, 9, 17), (2026, 11, 5), (2026, 12, 17),
    (2027, 2, 4),  (2027, 3, 18), (2027, 5, 6),  (2027, 6, 17),
    (2027, 8, 5),  (2027, 9, 16), (2027, 11, 4), (2027, 12, 16),
}

ECB_DATES = {
    (2025, 1, 30), (2025, 3, 6),  (2025, 4, 17), (2025, 6, 5),
    (2025, 7, 24), (2025, 9, 11), (2025, 10, 30),(2025, 12, 11),
    (2026, 1, 29), (2026, 3, 5),  (2026, 4, 16), (2026, 6, 4),
    (2026, 7, 23), (2026, 9, 10), (2026, 10, 29),(2026, 12, 10),
    (2027, 1, 28), (2027, 3, 4),  (2027, 4, 15), (2027, 6, 3),
    (2027, 7, 22), (2027, 9, 9),  (2027, 10, 28),(2027, 12, 9),
}


def _is_first_friday(dt: datetime) -> bool:
    return dt.weekday() == 4 and dt.day <= 7


def _in_daily_window(now: datetime) -> tuple[bool, str]:
    now_mins = now.hour * 60 + now.minute
    for (sh, sm, eh, em, label) in DAILY_BLOCKED_WINDOWS:
        start = sh * 60 + sm
        end   = eh * 60 + em
        if start <= now_mins < end:
            h12     = eh % 12 or 12
            ampm    = "AM" if eh < 12 else "PM"
            end_fmt = f"{h12}:{em:02d} {ampm}"
            return True, f"{label} — resumes {end_fmt} UTC"
    return False, ""


def _static_global_blocked() -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    key = (now.year, now.month, now.day)
    if _is_first_friday(now):
        return True, "NFP Friday — US Non-Farm Payrolls — no trading all day"
    if key in FED_DATES:
        return True, "Fed rate decision day — no trading all day"
    blocked, reason = _in_daily_window(now)
    if blocked:
        return True, reason
    return False, ""


def _static_symbol_blocked(symbol: str) -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    key = (now.year, now.month, now.day)
    if key in BOE_DATES and symbol in GBP_PAIRS:
        return True, f"BoE MPC decision day — {symbol} blocked"
    if key in ECB_DATES and symbol in EUR_PAIRS:
        return True, f"ECB rate decision day — {symbol} blocked"
    return False, ""


# ── Public API (same interface as news_filter.py) ─────────────────────────────

def is_global_blocked() -> tuple[bool, str]:
    """
    Check if ALL pairs should be blocked right now.

    Tries the live service first. Falls back to static calendar.
    The live service is queried on USD/JPY as a representative US-event proxy
    for the global check — if there's a US market-stopping event (NFP, Fed),
    USD/JPY will be blocked and we surface that as a global block.
    """
    # First, always check the static calendar for absolute certainty
    # on the highest-stakes events (NFP day, Fed day) — these never
    # require a live service call to verify.
    static_blocked, static_reason = _static_global_blocked()

    # Query live service for USD/JPY as a global US-event proxy
    live_data = _get_pair_impact("USD/JPY")

    if live_data is not None:
        if live_data.get("blocked"):
            reason = live_data.get("reason", "High-impact event — no trading")
            return True, f"[LIVE] {reason}"

        # Even if live says not blocked, trust static for known critical dates
        if static_blocked:
            return True, f"[STATIC] {static_reason}"

        return False, ""

    # Live service unreachable — fall back to static
    return static_blocked, (f"[STATIC] {static_reason}" if static_blocked else "")


def is_symbol_blocked(symbol: str) -> tuple[bool, str]:
    """
    Check if a specific pair should be blocked right now.

    Tries the live service first for a precise per-pair impact score.
    Falls back to static calendar.

    Live integration benefit: detects mid-tier events (CPI, retail sales,
    PMI) that the static calendar misses entirely.
    """
    live_data = _get_pair_impact(symbol)

    if live_data is not None:
        if live_data.get("blocked"):
            reason = live_data.get("reason", "High-impact event")
            return True, f"[LIVE] {reason}"
        return False, ""

    # Live service unreachable — fall back to static
    return _static_symbol_blocked(symbol)


def get_pair_confidence_penalty(symbol: str) -> int:
    """
    NEW function (not in original news_filter.py).

    Returns the current confidence penalty for a pair (0 if no active event).
    The engine can use this to dynamically raise MIN_CONFIDENCE without
    fully blocking the pair.

    Example usage in the engine scan loop:
        from news_filter_live import get_pair_confidence_penalty
        ...
        penalty = get_pair_confidence_penalty(sym)
        effective_min = config.MIN_CONFIDENCE + penalty
        signals = [s for s in strategy_scores if s["score"] >= effective_min]
    """
    live_data = _get_pair_impact(symbol)
    if live_data is not None:
        return live_data.get("confidence_penalty", 0)
    return 0


def is_safe_to_trade(symbol: str = "") -> tuple[bool, str]:
    """Legacy convenience entry point — kept for backward compatibility."""
    blocked, reason = is_global_blocked()
    if blocked:
        return False, reason
    if symbol:
        blocked, reason = is_symbol_blocked(symbol)
        if blocked:
            return False, reason
    return True, ""


def get_upcoming_blocked_days(days: int = 30) -> list[dict]:
    """
    Returns upcoming blocked dates from the live service if available,
    otherwise falls back to the static calendar hardcoded dates.
    """
    try:
        r = requests.get(
            f"{NEWS_IMPACT_URL}/api/impact/upcoming",
            params={"hours": days * 24},
            timeout=SERVICE_TIMEOUT,
        )
        r.raise_for_status()
        data   = r.json()
        events = data.get("events", [])

        # Format to match the original news_filter.py return shape
        result = []
        for e in events:
            if e.get("impact_level", 0) >= 8:    # only high-impact shown in calendar
                result.append({
                    "date":          e.get("scheduled_utc", "")[:10],
                    "event":         e.get("event", ""),
                    "scope":         "all_pairs" if e.get("impact_level", 0) >= 9 else "specific_pairs",
                    "pairs_blocked": ", ".join(e.get("affects_pairs", [])),
                    "impact_level":  e.get("impact_level", 0),
                })
        return result
    except Exception:
        pass

    # Static fallback
    now    = datetime.now(timezone.utc)
    result = []
    for offset in range(days + 1):
        dt  = now + timedelta(days=offset)
        key = (dt.year, dt.month, dt.day)
        date_str = dt.strftime("%Y-%m-%d")
        weekday  = dt.strftime("%A")
        if _is_first_friday(dt):
            result.append({"date": date_str, "weekday": weekday,
                           "event": "NFP Friday", "scope": "all_pairs", "pairs_blocked": "ALL"})
        if key in FED_DATES:
            result.append({"date": date_str, "weekday": weekday,
                           "event": "Fed Rate Decision", "scope": "all_pairs", "pairs_blocked": "ALL"})
        if key in BOE_DATES:
            result.append({"date": date_str, "weekday": weekday,
                           "event": "BoE MPC Rate Decision", "scope": "gbp_pairs", "pairs_blocked": "GBP/USD"})
        if key in ECB_DATES:
            result.append({"date": date_str, "weekday": weekday,
                           "event": "ECB Rate Decision", "scope": "eur_pairs", "pairs_blocked": "EUR/USD"})
    result.sort(key=lambda x: x["date"])
    return result
