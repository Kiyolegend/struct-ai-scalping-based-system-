"""
Confluence Scoring Engine — Scalping Edition.

Every trade signal earns points based on how many conditions are aligned.
A trade is only taken if the total score is >= MINIMUM_CONFIDENCE (70).

Scoring table:
  Bias alignment (1H + 15M same direction)    → +30
  Zone presence (price near supply/demand)    → +20
  BOS confirmation (break of structure)       → +20
  Session match (London or NY)                → +10
  Clean structure (LH or HL on 5M)           → +10
  Precision factor (scalping-specific entry)  → +10
  ─────────────────────────────────────────────────
  Maximum                                     = 100
  Minimum to trade                            = 70

Usage:
    score, breakdown = calculate(state, trade_type, conditions)
"""

MINIMUM_CONFIDENCE = 70

WEIGHTS = {
    "bias_aligned":      30,
    "zone_present":      20,
    "bos_confirmed":     20,
    "session_match":     10,
    "clean_structure":   10,
    "precision_factor":  10,
}


def calculate(state: dict, trade_type: str, conditions: dict) -> tuple[int, dict]:
    breakdown = {}
    total = 0

    for key, weight in WEIGHTS.items():
        passed = bool(conditions.get(key, False))
        points = weight if passed else 0
        breakdown[key] = {"passed": passed, "points": points}
        total += points

    return total, breakdown


def tradeable(score: int) -> bool:
    return score >= MINIMUM_CONFIDENCE


def format_breakdown(score: int, breakdown: dict) -> str:
    parts = []
    for key, info in breakdown.items():
        if info["passed"]:
            parts.append(f"{key}(+{info['points']})")
    return f"score={score} [{', '.join(parts)}]"
