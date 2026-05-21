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
    First Friday of every month  — US NFP (skip all day)
    Known Fed decision dates      — skip all day
    Known BoE decision dates      — skip all day
    Known ECB decision dates      — skip all day
"""

from datetime import datetime, timezone


# ── Layer 1: Daily recurring blocked windows (UTC) ────────────────────────────
# Each entry: (start_hour, start_min, end_hour, end_min, label)
DAILY_BLOCKED_WINDOWS = [
    (6,  45,  8, 30, "UK/EU data window (CPI, GDP, PMI, employment)"),
    (12, 15, 13, 30, "US data window (CPI, NFP, retail sales, GDP)"),
    (13, 30, 14, 30, "Fed speaker / FOMC window"),
]


# ── Layer 2: Special full-day block dates (year, month, day) ─────────────────
# Fed decision dates 2025–2026 (from federalreserve.gov)
FED_DATES = {
    (2025, 1, 29), (2025, 3, 19), (2025, 5, 7),  (2025, 6, 18),
    (2025, 7, 30), (2025, 9, 17), (2025, 11, 5), (2025, 12, 17),
    (2026, 1, 28), (2026, 3, 18), (2026, 5, 6),  (2026, 6, 17),
    (2026, 7, 29), (2026, 9, 16), (2026, 11, 4), (2026, 12, 16),
}

# BoE MPC decision dates 2025–2026
BOE_DATES = {
    (2025, 2, 6),  (2025, 3, 20), (2025, 5, 8),  (2025, 6, 19),
    (2025, 8, 7),  (2025, 9, 18), (2025, 11, 6), (2025, 12, 18),
    (2026, 2, 5),  (2026, 3, 19), (2026, 5, 7),  (2026, 6, 18),
    (2026, 8, 6),  (2026, 9, 17), (2026, 11, 5), (2026, 12, 17),
}

# ECB rate decision dates 2025–2026
ECB_DATES = {
    (2025, 1, 30), (2025, 3, 6),  (2025, 4, 17), (2025, 6, 5),
    (2025, 7, 24), (2025, 9, 11), (2025, 10, 30),(2025, 12, 11),
    (2026, 1, 29), (2026, 3, 5),  (2026, 4, 16), (2026, 6, 4),
    (2026, 7, 23), (2026, 9, 10), (2026, 10, 29),(2026, 12, 10),
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


def _is_special_day(now: datetime) -> tuple[bool, str]:
    """Check if today is NFP Friday or a central bank decision day."""
    key = (now.year, now.month, now.day)
    if _is_first_friday(now):
        return True, "NFP Friday — US Non-Farm Payrolls — no trading all day"
    if key in FED_DATES:
        return True, "Fed rate decision day — no trading all day"
    if key in BOE_DATES:
        return True, "BoE MPC decision day — GBP pairs highly volatile"
    if key in ECB_DATES:
        return True, "ECB rate decision day — EUR pairs highly volatile"
    return False, ""


def is_safe_to_trade() -> tuple[bool, str]:
    """
    Main entry point. Returns (True, "") when safe to trade,
    or (False, reason_string) when blocked.

    Call this at the top of every scan cycle before running any strategy.
    Read-only — zero side effects on any other engine state.
    """
    now = datetime.now(timezone.utc)

    blocked, reason = _is_special_day(now)
    if blocked:
        return False, reason

    blocked, reason = _in_daily_window(now)
    if blocked:
        return False, reason

    return True, ""