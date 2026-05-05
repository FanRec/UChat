from __future__ import annotations

import threading
from typing import Any


class MockBodyBackend:
    def __init__(self, *, body_id: str) -> None:
        self.body_id = body_id
        self._lock = threading.Lock()
        self._operation_count = 0
        self._last_hotkeys: list[str] = []
        self._tracking_state: dict[str, float] = {}

    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    def probe(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend_ready": True,
                "backend_type": "mock",
                "body_id": self.body_id,
                "operation_count": self._operation_count,
                "tracking_state": dict(self._tracking_state),
                "last_hotkeys": list(self._last_hotkeys[-5:]),
            }

    def trigger_hotkey(self, hotkey: str) -> dict[str, Any]:
        with self._lock:
            self._operation_count += 1
            self._last_hotkeys.append(hotkey)
        return {"triggered": hotkey, "backend": "mock"}

    def apply_tracking(self, values: dict[str, float], *, source: str) -> dict[str, Any]:
        with self._lock:
            self._operation_count += 1
            for key, value in values.items():
                self._tracking_state[key] = round(float(value), 4)
        return {"applied": dict(values), "source": source, "backend": "mock"}
