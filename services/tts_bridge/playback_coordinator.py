from __future__ import annotations

import threading
from typing import Any, Callable

from services.tts_bridge.audio_playback import playback_timeout_ms


class PlaybackCoordinator:
    def __init__(self, *, lock: threading.Lock, player, sessions_by_trace: dict[str, Any], streaming_config) -> None:
        self._lock = lock
        self._player = player
        self._sessions_by_trace = sessions_by_trace
        self._streaming_config = streaming_config
        self._playback_lock = threading.Lock()

    def play_ready_segment(
        self,
        *,
        trace_id: str,
        generation_id: int,
        segment_index: int,
        playback_runner: Callable[[Any], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self._player.ready:
            return {"playback_started": False, "playback_completed": False}
        while True:
            with self._lock:
                session = self._sessions_by_trace.get(trace_id)
                if session is None or session.generation_id != generation_id or session.closed:
                    return {"playback_started": False, "playback_completed": False}
                record = session.segments.get(segment_index)
                if record is None:
                    return {"playback_started": False, "playback_completed": False}
                if record.state in {"obsolete", "cancelled", "failed"}:
                    return {"playback_started": False, "playback_completed": False}
                if session.next_play_index != segment_index or record.state != "ready" or record.asset is None:
                    condition = session.condition
                    if condition is None:
                        return {"playback_started": False, "playback_completed": False}
                    condition.wait(timeout=0.05)
                    continue
                record.state = "playing"
                asset = record.asset
                break
        with self._playback_lock:
            result = {"playback_started": True, "playback_completed": False}
            if playback_runner is not None:
                result.update(playback_runner(asset))
            elif asset.kind == "pcm":
                playback_completed = self._player.play_pcm_and_wait(
                    pcm_bytes=asset.pcm_bytes or b"",
                    sample_rate=asset.sample_rate,
                    timeout_ms=playback_timeout_ms(asset.duration_ms, self._streaming_config.drain_timeout_ms),
                )
                result.update(self._player.stream_stats())
                result["playback_completed"] = playback_completed
            else:
                if asset.artifact_path is None:
                    return {"playback_started": False, "playback_completed": False}
                if callable(getattr(self._player, "play_file_as_pcm_and_wait", None)):
                    playback_completed = self._player.play_file_as_pcm_and_wait(
                        path=asset.artifact_path,
                        timeout_ms=playback_timeout_ms(asset.duration_ms, self._streaming_config.drain_timeout_ms),
                    )
                else:
                    playback_completed = self._player.enqueue_and_wait(
                        asset.artifact_path,
                        wait=True,
                        timeout_ms=playback_timeout_ms(asset.duration_ms, self._streaming_config.drain_timeout_ms),
                    )
                result["playback_completed"] = playback_completed
            with self._lock:
                session = self._sessions_by_trace.get(trace_id)
                if session is not None and session.generation_id == generation_id:
                    record = session.segments.get(segment_index)
                    if record is not None and record.state == "playing":
                        record.state = "completed"
                    if session.next_play_index == segment_index:
                        session.next_play_index += 1
                    if session.condition is not None:
                        session.condition.notify_all()
            return result

    def finalize_late_result(
        self,
        *,
        is_current_generation: bool,
        playback_result: dict[str, Any],
    ) -> dict[str, Any]:
        if is_current_generation:
            return playback_result
        return {
            "playback_started": False,
            "playback_completed": False,
            "late_result_discarded": True,
        }
