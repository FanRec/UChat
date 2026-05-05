from __future__ import annotations

import threading
import time
from typing import Callable


class IdleEngine:
    def __init__(self, *, tick_hz: float, on_tick: Callable[[], None]) -> None:
        self.tick_hz = max(0.5, tick_hz)
        self.on_tick = on_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="body-service-idle", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        interval = 1.0 / self.tick_hz
        while not self._stop.wait(interval):
            self.on_tick()
