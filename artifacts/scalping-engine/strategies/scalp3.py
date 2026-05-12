"""
Scalping Strategy 3 — PLACEHOLDER

See scalp1.py for full documentation on the return format,
available state keys, and confluence scoring system.

This strategy is ready to receive logic.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from confluence.confidence_score import calculate, tradeable, format_breakdown


def check(state: dict, debug: bool = False) -> dict | None:
    """PLACEHOLDER — insert scalping logic here. Returns None until defined."""
    if state is None:
        return None
    return None
