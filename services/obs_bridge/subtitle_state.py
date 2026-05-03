from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SubtitleLineState:
    task_id: str
    segment_index: int
    text: str
    revealed_text: str = ""
    revealed_count: int = 0
    playback_started: bool = False
    playback_completed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "segment_index": self.segment_index,
            "text": self.text,
            "revealed_text": self.revealed_text,
            "revealed_count": self.revealed_count,
            "playback_started": self.playback_started,
            "playback_completed": self.playback_completed,
        }


@dataclass
class SubtitleSessionState:
    trace_id: str
    generation_id: int
    status: str = "active"
    history: list[SubtitleLineState] = field(default_factory=list)
    active_line: SubtitleLineState | None = None
    ended_at_generation: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "generation_id": self.generation_id,
            "status": self.status,
            "history": [line.to_dict() for line in self.history],
            "active_line": self.active_line.to_dict() if self.active_line is not None else None,
            "ended_at_generation": self.ended_at_generation,
        }


class SubtitleStateStore:
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, int], SubtitleSessionState] = {}

    def apply(self, body: dict[str, Any]) -> SubtitleSessionState | None:
        trace_id = str(body.get("trace_id", ""))
        metadata = dict(body.get("metadata") or {})
        generation_id = int(metadata.get("generation_id", 1) or 1)
        action = str(body.get("action", "sentence"))
        text = str(body.get("text", ""))
        task_id = str(body.get("task_id", body.get("outbound_id", "")))
        segment_index = int(metadata.get("segment_index", metadata.get("sentence_index", 0)) or 0)
        key = (trace_id, generation_id)

        if action == "clear":
            self._clear_session(trace_id=trace_id, generation_id=generation_id)
            return None

        self._drop_older_generations(trace_id=trace_id, generation_id=generation_id)
        session = self._sessions.get(key)
        if session is None:
            self._clear_other_sessions(except_key=key)
            session = SubtitleSessionState(trace_id=trace_id, generation_id=generation_id)
            self._sessions[key] = session

        if action in {"segment_start", "segment_progress", "segment_complete", "sentence"}:
            session.status = "active"
            session.ended_at_generation = None
            line = session.active_line
            if line is None or line.segment_index != segment_index:
                if session.active_line is not None:
                    session.history.append(session.active_line)
                line = SubtitleLineState(task_id=task_id, segment_index=segment_index, text=text)
                session.active_line = line
            line.text = text or line.text
            line.revealed_text = str(metadata.get("revealed_text", text if action == "sentence" else line.revealed_text))
            line.revealed_count = int(metadata.get("revealed_count", len(line.revealed_text)) or 0)
            line.playback_started = bool(metadata.get("playback_started", action != "sentence"))
            line.playback_completed = bool(metadata.get("playback_completed", action in {"segment_complete", "sentence"}))
            if action == "segment_complete":
                session.history.append(line)
                session.active_line = None
        elif action == "turn_end":
            session.status = "ended"
            session.ended_at_generation = generation_id

        return session

    def active_sessions(self) -> list[SubtitleSessionState]:
        sessions = [session for session in self._sessions.values() if session.status == "active"]
        return sorted(sessions, key=lambda item: (item.trace_id, item.generation_id))

    def _drop_older_generations(self, *, trace_id: str, generation_id: int) -> None:
        for key in [item for item in self._sessions if item[0] == trace_id and item[1] < generation_id]:
            self._sessions.pop(key, None)

    def _clear_session(self, *, trace_id: str, generation_id: int) -> None:
        keys = [item for item in self._sessions if item[0] == trace_id and item[1] <= generation_id]
        for key in keys:
            self._sessions.pop(key, None)

    def _clear_other_sessions(self, *, except_key: tuple[str, int]) -> None:
        keys = [item for item in self._sessions if item != except_key]
        for key in keys:
            self._sessions.pop(key, None)
