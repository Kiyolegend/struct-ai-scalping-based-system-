"""
Signal Memory — prevents the same setup firing more than once per session.
Stores one key PER SYMBOL so parallel multi-symbol scanning doesn't
overwrite keys across symbols.
"""

import json
import os
import threading


class SignalMemory:
    def __init__(self):
        self._path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_memory.json")
        self._lock = threading.Lock()
        self._keys = {}
        self._load()

    def _load(self):
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and "keys" in data:
                self._keys = {
                    sym: {"key": tuple(v["key"]), "bias": v["bias"]}
                    for sym, v in data["keys"].items() if v.get("key")
                }
            elif isinstance(data, dict) and "key" in data:
                key = data.get("key")
                if key:
                    sym = key[0]
                    self._keys = {sym: {"key": tuple(key), "bias": data.get("bias")}}
        except Exception:
            self._keys = {}

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump({"keys": {
                    sym: {"key": list(v["key"]), "bias": v["bias"]}
                    for sym, v in self._keys.items()
                }}, f)
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
        with self._lock:
            symbol = decision.get("symbol", "")
            entry  = self._keys.get(symbol)
            if entry is None:
                return False
            new_key  = self._make_key(decision)
            new_bias = state.get("bias", {}).get("1h", "neutral")
            if new_key != entry["key"]:
                return False
            if new_bias != entry["bias"]:
                del self._keys[symbol]
                self._save()
                return False
            return True

    def record(self, decision: dict, state: dict):
        with self._lock:
            sym = decision.get("symbol", "")
            self._keys[sym] = {
                "key":  self._make_key(decision),
                "bias": state.get("bias", {}).get("1h", "neutral"),
            }
            self._save()

    def clear(self):
        with self._lock:
            self._keys = {}
            self._save()