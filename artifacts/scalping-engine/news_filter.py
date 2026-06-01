"""
STRUCT.ai Scalping Engine — News Filter
=======================================
Blocks the engine from firing new trades during high-impact
economic news windows. Two layers:

  Layer 1 — Daily recurring windows (UTC):
    UK/EU window : 6:45 AM – 8:30 AM   (London open + UK/EU data)
    US window    : 12:15 PM – 1:30 PM  (US data + NY open)
    Fed window   : 1:30 PM – 2:30 PM   (Fed speakers / FOMC)

  Layer 2 — Special days:
    First Friday of every month  — US NFP (skip all day, ALL pairs)
    Known Fed decision dates      — skip all day, ALL pairs
    Known BoE decision dates      — GBP/USD only
    Known ECB decision dates      — EUR/USD only
"""

from datetime import datetime, timezone, timedelta


# ── Which pairs each central bank primarily affects ───────────────────────────
GBP_PAIRS = {"GBP/USD"}
EUR_PAIRS = {"EUR/USD"}


# ── Layer 1: Daily recurring blocked windows (UTC) ────────────────────────────
# Each entry: (start_hour, start_min, end_hour, end_min, label)
DAILY_BLOCKED_WINDOWS = [
    (6,  45,  8, 30, "UK/EU data window (CPI, GDP, PMI, employment)"),
    (12, 15, 13, 30, "US data window (CPI, NFP, retail sales, GDP)"),
    (13, 30, 14, 30, "Fed speaker / FOMC window"),
]


# ── Layer 2: Special full-day block dates (year, month, day) ─────────────────
# Fed decision dates 2025–2027 (from federalreserve.gov)
FED_DATES = {
    (2025, 1, 29), (2025, 3, 19), (2025, 5, 7),  (2025, 6, 18),
    (2025, 7, 30), (2025, 9, 17), (2025, 11, 5), (2025, 12, 17),
    (2026, 1, 28), (2026, 3, 18), (2026, 5, 6),  (2026, 6, 17),
    (2026, 7, 29), (2026, 9, 16), (2026, 11, 4), (2026, 12, 16),
    # 2027 — based on published Fed schedule (verify at federalreserve.gov)
    (2027, 1, 27), (2027, 3, 17), (2027, 5, 5),  (2027, 6, 16),
    (2027, 7, 28), (2027, 9, 15), (2027, 11, 3), (2027, 12, 15),
}

# BoE MPC decision dates 2025–2027 — blocks GBP/USD only
BOE_DATES = {
    (2025, 2, 6),  (2025, 3, 20), (2025, 5, 8),  (2025, 6, 19),
    (2025, 8, 7),  (2025, 9, 18), (2025, 11, 6), (2025, 12, 18),
    (2026, 2, 5),  (2026, 3, 19), (2026, 5, 7),  (2026, 6, 18),
    (2026, 8, 6),  (2026, 9, 17), (2026, 11, 5), (2026, 12, 17),
    # 2027 — based on BoE MPC published schedule (verify at bankofengland.co.uk)
    (2027, 2, 4),  (2027, 3, 18), (2027, 5, 6),  (2027, 6, 17),
    (2027, 8, 5),  (2027, 9, 16), (2027, 11, 4), (2027, 12, 16),
}

# ECB rate decision dates 2025–2027 — blocks EUR/USD only
ECB_DATES = {
    (2025, 1, 30), (2025, 3, 6),  (2025, 4, 17), (2025, 6, 5),
    (2025, 7, 24), (2025, 9, 11), (2025, 10, 30),(2025, 12, 11),
    (2026, 1, 29), (2026, 3, 5),  (2026, 4, 16), (2026, 6, 4),
    (2026, 7, 23), (2026, 9, 10), (2026, 10, 29),(2026, 12, 10),
    # 2027 — based on ECB published schedule (verify at ecb.europa.eu)
    (2027, 1, 28), (2027, 3, 4),  (2027, 4, 15), (2027, 6, 3),
    (2027, 7, 22), (2027, 9, 9),  (2027, 10, 28),(2027, 12, 9),
}


def _is_first_friday(dt: datetime) -> bool:
    """Returns True if dt falls on the first Friday of its month (NFP day)."""
    return dt.weekday() == 4 and dt.day <= 7


def _in_daily_window(now: datetime) -> tuple[bool, str]:
    """Check if current UTC time falls in any recurring blocked window."""
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


def is_global_blocked(at_ts=None) -> tuple[bool, str]:
    """
    Check events that block ALL pairs regardless of symbol:
      — NFP Fridays
      — Fed decision days
      — Daily recurring time windows

    BoE and ECB are NOT included here — use is_symbol_blocked() for those.
    """
    now = datetime.fromtimestamp(at_ts, tz=timezone.utc) if at_ts else datetime.now(timezone.utc)
    key = (now.year, now.month, now.day)

    if _is_first_friday(now):
        return True, "NFP Friday — US Non-Farm Payrolls — no trading all day"
    if key in FED_DATES:
        return True, "Fed rate decision day — no trading all day"

    blocked, reason = _in_daily_window(now)
    if blocked:
        return True, reason

    return False, ""


def is_symbol_blocked(symbol: str, at_ts=None) -> tuple[bool, str]:
    """
    Check events that only block specific currency pairs:
      — BoE MPC days  → blocks GBP/USD only
      — ECB days      → blocks EUR/USD only

    Returns (True, reason) if this symbol should be skipped right now.
    All other symbols pass through on BoE/ECB days and continue scanning.
    """
    now = datetime.fromtimestamp(at_ts, tz=timezone.utc) if at_ts else datetime.now(timezone.utc)
    key = (now.year, now.month, now.day)

    if key in BOE_DATES and symbol in GBP_PAIRS:
        return True, f"BoE MPC decision day — {symbol} blocked (GBP extremely volatile)"
    if key in ECB_DATES and symbol in EUR_PAIRS:
        return True, f"ECB rate decision day — {symbol} blocked (EUR extremely volatile)"

    return False, ""


def is_safe_to_trade(symbol: str = "", at_ts=None) -> tuple[bool, str]:
    """
    Legacy / convenience entry point — combines global + per-symbol checks.
    Kept for backward compatibility.

    For the engine's per-symbol scanning, prefer calling:
      is_global_blocked()    once per cycle
      is_symbol_blocked(sym) once per symbol inside the scan loop
    """
    blocked, reason = is_global_blocked(at_ts=at_ts)


    if blocked:
        return False, reason

    if symbol:
        blocked, reason = is_symbol_blocked(symbol, at_ts=at_ts)
        if blocked:
            return False, reason
    else:
        # No symbol provided — conservative fallback: block all on BoE/ECB days
        now = datetime.fromtimestamp(at_ts, tz=timezone.utc) if at_ts else datetime.now(timezone.utc)
        key = (now.year, now.month, now.day)
        if key in BOE_DATES:
            return False, "BoE MPC decision day — ALL pairs blocked (GBP pairs extremely volatile, rest of market erratic)"
        if key in ECB_DATES:
            return False, "ECB rate decision day — ALL pairs blocked (EUR pairs extremely volatile, rest of market erratic)"

    return True, ""


def get_upcoming_blocked_days(days: int = 30, at_ts=None) -> list[dict]:
    """
    Return a list of upcoming blocked dates within the next `days` days.
    Each entry: {date, weekday, event, scope, pairs_blocked}

    Used by the /api/news/upcoming dashboard endpoint.
    Daily recurring windows are excluded (they apply every day by definition).
    """
    now    = datetime.fromtimestamp(at_ts, tz=timezone.utc) if at_ts else datetime.now(timezone.utc)
    result = []

    for offset in range(days + 1):
        dt  = now + timedelta(days=offset)
        key = (dt.year, dt.month, dt.day)
        date_str = dt.strftime("%Y-%m-%d")
        weekday  = dt.strftime("%A")

        if _is_first_friday(dt):
            result.append({
                "date":          date_str,
                "weekday":       weekday,
                "event":         "NFP Friday — US Non-Farm Payrolls",
                "scope":         "all_pairs",
                "pairs_blocked": "ALL",
            })

        if key in FED_DATES:
            result.append({
                "date":          date_str,
                "weekday":       weekday,
                "event":         "Fed Rate Decision (FOMC)",
                "scope":         "all_pairs",
                "pairs_blocked": "ALL",
            })

        if key in BOE_DATES:
            result.append({
                "date":          date_str,
                "weekday":       weekday,
                "event":         "BoE MPC Rate Decision",
                "scope":         "gbp_pairs",
                "pairs_blocked": ", ".join(sorted(GBP_PAIRS)),
            })

        if key in ECB_DATES:
            result.append({
                "date":          date_str,
                "weekday":       weekday,
                "event":         "ECB Rate Decision",
                "scope":         "eur_pairs",
                "pairs_blocked": ", ".join(sorted(EUR_PAIRS)),
            })

    result.sort(key=lambda x: x["date"])
    return result