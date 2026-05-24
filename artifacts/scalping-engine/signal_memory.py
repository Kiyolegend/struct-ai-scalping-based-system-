"""
Signal Memory — prevents the same setup firing more than once per session.

Stores one key PER SYMBOL PER STRATEGY so that:
  - parallel multi-symbol scanning doesn't overwrite keys across symbols, and
  - one strategy firing for a symbol doesn't erase the memory of a different
    strategy that already fired for the same symbol.
"""

import json
import os
import threading


class SignalMemory:
    def __init__(self):
        self._path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_memory.json")
        self._lock = threading.Lock()
        # _keys[symbol][strategy] = {"key": tuple, "bias": str}
        self._keys: dict[str, dict[str, dict]] = {}
        self._load()

    def _load(self):
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and "keys" in data:
                raw = data["keys"]
                self._keys = {}
                for sym, val in raw.items():
                    # New format: {sym: {strategy_name: {key, bias}}}
                    if isinstance(val, dict) and all(
                        isinstance(v, dict) and "key" in v
                        for v in val.values()
                    ):
                        self._keys[sym] = {
                            strat: {"key": tuple(v["key"]), "bias": v["bias"]}
                            for strat, v in val.items()
                            if v.get("key")
                        }
                    # Old format (single entry per symbol): {sym: {key, bias}}
                    elif isinstance(val, dict) and "key" in val and val.get("key"):
                        k = tuple(val["key"])
                        strat = k[1] if len(k) > 1 else ""
                        self._keys[sym] = {
                            strat: {"key": k, "bias": val.get("bias")}
                        }
        except Exception:
            self._keys = {}

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump({"keys": {
                    sym: {
                        strat: {"key": list(v["key"]), "bias": v["bias"]}
                        for strat, v in strategies.items()
                    }
                    for sym, strategies in self._keys.items()
                }}, f)
        except Exception:
            pass

    def _make_key(self, decision: dict) -> tuple:
        entry = decision.get("entry", 0)
        # Bucket entry into 10-pip zones so that normal 1–5 pip price drift between
        # scan cycles does not create a new key (and bypass dedup) each cycle.
        # A genuine re-entry 10+ pips away from the last recorded entry gets a
        # different bucket and is correctly treated as a new setup.
        # JPY pairs trade at ~100–200: 10 pips = 0.1 → round to 1dp
        # All other pairs trade at <10:  10 pips = 0.001 → round to 3dp
        entry_bucket = round(entry, 1) if entry > 50 else round(entry, 3)
        return (
            decision.get("symbol", ""),
            decision.get("strategy", ""),
            decision.get("type", ""),
            round(decision.get("sl", 0), 5),
            entry_bucket,
        )

    def is_duplicate(self, decision: dict, state: dict) -> bool:
        with self._lock:
            symbol      = decision.get("symbol", "")
            strategy    = decision.get("strategy", "")
            sym_entries = self._keys.get(symbol)
            if sym_entries is None:
                return False
            entry = sym_entries.get(strategy)
            if entry is None:
                return False
            new_key  = self._make_key(decision)
            new_bias = state.get("bias", {}).get("1h", "neutral")
            if new_key != entry["key"]:
                return False
            if new_bias != entry["bias"]:
                sym_entries.pop(strategy, None)
                if not sym_entries:
                    del self._keys[symbol]
                self._save()
                return False
            return True

    def record(self, decision: dict, state: dict):
        with self._lock:
            sym      = decision.get("symbol", "")
            strategy = decision.get("strategy", "")
            if sym not in self._keys:
                self._keys[sym] = {}
            self._keys[sym][strategy] = {
                "key":  self._make_key(decision),
                "bias": state.get("bias", {}).get("1h", "neutral"),
            }
            self._save()

    def clear(self):
        with self._lock:
            self._keys = {}
            self._save()