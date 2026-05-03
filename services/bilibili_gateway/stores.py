from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass
class EventEnvelope:
    sequence: int
    payload: dict[str, Any]


class EventStore:
    def __init__(self, *, max_events: int, cursor_retention: int) -> None:
        self.max_events = max_events
        self.cursor_retention = cursor_retention
        self._events: deque[EventEnvelope] = deque()
        self._sequence = 0

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        payload["cursor"] = str(self._sequence)
        self._events.append(EventEnvelope(sequence=self._sequence, payload=payload))
        keep = max(self.max_events, self.cursor_retention)
        while len(self._events) > keep:
            self._events.popleft()
        return payload

    def poll(self, *, cursor: str, limit: int) -> tuple[list[dict[str, Any]], str, bool]:
        last_seen = int(cursor) if cursor.strip() else 0
        earliest = self._events[0].sequence if self._events else 0
        cursor_expired = bool(self._events) and last_seen < earliest - 1
        events: list[dict[str, Any]] = []
        next_cursor = str(last_seen)
        for envelope in self._events:
            if envelope.sequence <= last_seen:
                continue
            events.append(dict(envelope.payload))
            next_cursor = str(envelope.sequence)
            if len(events) >= limit:
                break
        if cursor_expired and events:
            next_cursor = str(events[-1]["cursor"])
        return events, next_cursor, cursor_expired

    def clear(self) -> None:
        self._events.clear()

    def stats(self) -> dict[str, int]:
        return {
            "queued_events": len(self._events),
            "latest_cursor": self._sequence,
        }
