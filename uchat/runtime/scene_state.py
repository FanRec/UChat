from __future__ import annotations

from datetime import datetime
from dataclasses import replace

from uchat.config import RuntimeConfig, SceneDefaultsConfig
from uchat.contracts import NormalizedEvent, SceneState, SessionState


class SceneStateUpdater:
    def __init__(self, *, runtime: RuntimeConfig, defaults: SceneDefaultsConfig):
        active_platform = "console" if runtime.scene_kind == "console" else "bilibili"
        self._initial_state = SceneState(
            scene_kind=runtime.scene_kind,
            audience_scope=runtime.audience_scope,
            show_profile=defaults.show_profile,
            program_topic=defaults.program_topic,
            segment_topic=defaults.segment_topic,
            micro_topic=defaults.micro_topic,
            danmaku_velocity=defaults.danmaku_velocity,
            audience_density=defaults.audience_density,
            risk_level=defaults.risk_level,
            silence_level=defaults.silence_level,
            engagement_level=defaults.engagement_level,
            active_platform=active_platform,
        )

    def initial_state(self) -> SceneState:
        return replace(self._initial_state)

    def apply_event(
        self,
        scene_state: SceneState,
        event: NormalizedEvent,
        *,
        session_state: SessionState | None = None,
    ) -> SceneState:
        if event.source_type.startswith("bilibili_"):
            scene_state.active_platform = "bilibili"
            scene_state.danmaku_velocity = self._event_value(event, "danmaku_velocity", scene_state.danmaku_velocity)
            scene_state.audience_density = self._event_value(event, "audience_density", scene_state.audience_density)
            scene_state.risk_level = self._event_value(event, "risk_level", scene_state.risk_level)
            scene_state.engagement_level = self._event_value(event, "engagement_level", scene_state.engagement_level)
            scene_state.silence_level = self._compute_silence_level(scene_state, event, session_state)
            scene_state.program_topic = str(event.metadata.get("program_topic", scene_state.program_topic))
            scene_state.segment_topic = str(event.metadata.get("segment_topic", scene_state.segment_topic))
            scene_state.micro_topic = str(event.metadata.get("micro_topic", scene_state.micro_topic))
            return scene_state
        if scene_state.scene_kind != "live_stream":
            scene_state.active_platform = "console"
        return scene_state

    def _event_value(self, event: NormalizedEvent, key: str, fallback: str) -> str:
        value = str(event.metadata.get(key, "")).strip()
        return value or fallback

    def _compute_silence_level(
        self,
        scene_state: SceneState,
        event: NormalizedEvent,
        session_state: SessionState | None,
    ) -> str:
        if event.metadata.get("silence_level"):
            return str(event.metadata["silence_level"])
        if session_state is None:
            return scene_state.silence_level
        reference_time = session_state.last_input_at or session_state.last_reply_at
        if not reference_time:
            return "n/a"
        try:
            previous = datetime.fromisoformat(reference_time)
            current = datetime.fromisoformat(event.occurred_at)
        except ValueError:
            return scene_state.silence_level
        gap_seconds = max(0.0, (current - previous).total_seconds())
        if gap_seconds >= 90:
            return "high"
        if gap_seconds >= 45:
            return "normal"
        if gap_seconds >= 15:
            return "low"
        return "n/a"
