"""
Signal Memory — prevents the same trade setup firing multiple times per session.

# A setup is uniquely identified by (strategy, direction, SL rounded to 5 decimal places).
Once a trade fires, that key is locked until market structure changes:
  - 1H bias flips direction
  - SL level shifts (new structure creates a different SL)
  - Daily reset (new trading day)
"""

import json
import os


class SignalMemory:
    def __init__(self):
        self._path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_memory.json")
        self._key  = None
        self._bias = None
        self._load()

    def _load(self):
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            key        = data.get("key")
            self._key  = tuple(key) if key is not None else None
            self._bias = data.get("bias")
        except (FileNotFoundError, json.JSONDecodeError, Exception):
            self._key  = None
            self._bias = None

    def _save(self):
        try:
            data = {
                "key":  list(self._key) if self._key is not None else None,
                "bias": self._bias,
            }
            with open(self._path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

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
        self._save()

    def clear(self):
        self._key  = None
        self._bias = None
        self._save()