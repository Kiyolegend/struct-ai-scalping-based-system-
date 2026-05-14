"""
Signal Memory — prevents the same trade setup firing multiple times per session.

# A setup is uniquely identified by (strategy, direction, SL rounded to 5 decimal places).
Once a trade fires, that key is locked until market structure changes:
  - 1H bias flips direction
  - SL level shifts (new structure creates a different SL)
  - Daily reset (new trading day)
"""


class SignalMemory:
    def __init__(self):
        self._key  = None
        self._bias = None

    def _make_key(self, decision: dict) -> tuple:
        return (
            decision.get("symbol", ""),
            decision.get("strategy", ""),
            decision.get("type", ""),
            round(decision.get("sl", 0), 5),
        )

    def is_duplicate(self, decision: dict, state: dict) -> bool:
        if self._key is None:
            return False
        new_key  = self._make_key(decision)
        new_bias = state.get("bias", {}).get("1h", "neutral")
        if new_key != self._key:
            return False
        if new_bias != self._bias:
            self._key  = None
            self._bias = None
            return False
        return True

    def record(self, decision: dict, state: dict):
        self._key  = self._make_key(decision)
        self._bias = state.get("bias", {}).get("1h", "neutral")

    def clear(self):
        self._key  = None
        self._bias = None
