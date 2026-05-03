from __future__ import annotations

from uchat.config import TimingGateConfig
from uchat.contracts import NormalizedEvent, PlannerDecision, SessionState, new_decision_id
from uchat.output_queue import OutputQueue


class TimingGate:
    def __init__(self, config: TimingGateConfig | None = None) -> None:
        self.config = config or TimingGateConfig()

    def decide(
        self,
        event: NormalizedEvent,
        *,
        session_state: SessionState | None = None,
        output_queue: OutputQueue | None = None,
    ) -> PlannerDecision:
        text = str(event.metadata.get("reply_view_text", event.content_norm)).strip()
        moderation_result = dict(event.metadata.get("moderation_result", {}))
        if moderation_result:
            reply_policy = str(moderation_result.get("reply_policy", "")).strip()
            if reply_policy in {"skip", "drop"}:
                return PlannerDecision.skip(event.trace_id, "moderation reply policy skipped event")
            if reply_policy == "observe_only":
                return PlannerDecision(
                    decision_id=new_decision_id(),
                    trace_id=event.trace_id,
                    action="observe_only",
                    reasoning_summary="moderation marked event as observe_only",
                )
        if not text:
            return PlannerDecision.skip(event.trace_id, "empty console input")
        if text.lower() in {"/quit", "/exit"}:
            return PlannerDecision.quit(event.trace_id)
        if event.source_type.startswith("bilibili_"):
            return self._decide_live_event(event, text=text, session_state=session_state, output_queue=output_queue)
        return PlannerDecision(
            decision_id=new_decision_id(),
            trace_id=event.trace_id,
            action="reply",
            reasoning_summary="normal input for configured scene",
        )

    def _decide_live_event(
        self,
        event: NormalizedEvent,
        *,
        text: str,
        session_state: SessionState | None,
        output_queue: OutputQueue | None,
    ) -> PlannerDecision:
        scene_state = session_state.scene_state if session_state is not None else None
        moderation_view = event.moderation_view or {}
        moderation_result = dict(event.metadata.get("moderation_result", {}))
        reply_policy = str(moderation_view.get("reply_policy", "normal_reply"))
        if moderation_result:
            reply_policy = str(moderation_result.get("reply_policy", reply_policy))
        reply_value = float((event.message_value or {}).get("reply_value", 0.0) or 0.0)
        show_value = float((event.message_value or {}).get("show_value", 0.0) or 0.0)
        risk_score = float((event.message_value or {}).get("risk_score", 0.0) or 0.0)
        is_aggregated = bool((event.aggregation_info or {}).get("is_aggregated_event", False))
        moderation_labels = moderation_result.get("moderation_labels", moderation_view.get("moderation_labels", []))
        attack_intensity = str(moderation_result.get("attack_intensity", "none"))
        quote_allowed = bool(moderation_result.get("quote_allowed", moderation_view.get("quote_allowed", True)))
        if reply_policy in {"skip", "drop"}:
            return PlannerDecision.skip(event.trace_id, "gateway reply policy skipped event")
        if reply_policy == "observe_only":
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="observe_only",
                reasoning_summary="gateway marked event as observe_only",
            )
        if attack_intensity in {"medium", "high"} and not quote_allowed:
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="observe_only",
                reasoning_summary=f"moderation kept event on safe path ({attack_intensity}, labels={len(moderation_labels)})",
            )
        if event.intervention_candidate:
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="candidate_intervene",
                reasoning_summary="gateway marked event as intervention candidate",
            )
        lowered = text.lower()
        is_paid = bool(event.metadata.get("is_paid"))
        is_known_viewer = bool(event.metadata.get("is_known_viewer"))
        queue_depth = output_queue.depth if output_queue is not None else 0
        danmaku_velocity = str(event.metadata.get("danmaku_velocity") or getattr(scene_state, "danmaku_velocity", "n/a"))
        audience_density = str(event.metadata.get("audience_density") or getattr(scene_state, "audience_density", "n/a"))
        live_risk_level = str(event.metadata.get("risk_level") or getattr(scene_state, "risk_level", "L0"))
        silence_level = str(event.metadata.get("silence_level") or getattr(scene_state, "silence_level", "n/a"))
        engagement_level = str(event.metadata.get("engagement_level") or getattr(scene_state, "engagement_level", "n/a"))
        is_question = any(marker in text for marker in ("?", "？", "吗", "么", "啥", "什么", "为什么"))
        repeated = len(set(lowered)) == 1 and len(lowered) >= self.config.repeated_char_skip_min_length
        low_value = (
            len(text) <= self.config.low_value_max_length
            or repeated
            or lowered in self.config.low_value_literal_tokens
        )
        high_velocity = danmaku_velocity == "high"
        dense_audience = audience_density == "high"
        high_risk = live_risk_level in {"L2", "L3"}
        high_silence = silence_level == "high"
        low_engagement = engagement_level == "low"
        high_engagement = engagement_level == "high"

        if reply_value >= self.config.priority_reply_threshold or (is_paid and reply_value >= self.config.paid_reply_threshold):
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="reply",
                reasoning_summary=f"gateway reply value prioritized live event under {danmaku_velocity}/{audience_density}",
            )
        if risk_score >= self.config.risk_observe_only_threshold:
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="observe_only",
                reasoning_summary="high risk score kept event out of normal reply path",
            )
        if high_risk:
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="observe_only",
                reasoning_summary=f"scene risk level {live_risk_level} kept live event on conservative path",
            )
        if low_value and not is_known_viewer:
            return PlannerDecision.skip(event.trace_id, "low-value danmaku skipped")
        if (
            queue_depth >= self.config.busy_queue_depth_threshold or high_velocity
        ) and not is_question and not is_known_viewer and not is_paid:
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="listen_more",
                reasoning_summary=f"live scene is busy ({danmaku_velocity}, queue={queue_depth}), keep listening",
            )
        if session_state is not None:
            session_state.session_snapshot.current_output_occupancy = queue_depth
        if is_aggregated and show_value < self.config.aggregated_show_value_threshold:
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="observe_only",
                reasoning_summary="aggregated live event observed without direct reply",
            )
        if (
            dense_audience
            and reply_value < self.config.dense_audience_reply_threshold
            and not is_question
            and not is_known_viewer
            and not is_paid
        ):
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="listen_more",
                reasoning_summary="audience density is high, reserve reply slots for higher-value live events",
            )
        if (
            high_engagement
            and not is_question
            and reply_value < self.config.high_engagement_reply_threshold
            and not is_paid
        ):
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="wait",
                reasoning_summary="engagement is already high, avoid抢话 on medium-value live event",
            )
        if (
            high_silence
            and low_engagement
            and not high_velocity
            and not high_risk
            and reply_value >= self.config.cooling_keepalive_reply_threshold
        ):
            return PlannerDecision(
                decision_id=new_decision_id(),
                trace_id=event.trace_id,
                action="reply",
                reasoning_summary="live scene is cooling down, keep interaction alive with reply candidate",
            )
        return PlannerDecision(
            decision_id=new_decision_id(),
            trace_id=event.trace_id,
            action=(
                "reply"
                if (reply_value >= self.config.default_reply_threshold or is_question or is_known_viewer or len(text) >= 4)
                else "wait"
            ),
            reasoning_summary=(
                "live event prioritized by gateway value and scene state "
                f"({danmaku_velocity}/{audience_density}/{live_risk_level}/{silence_level}/{engagement_level})"
            ),
        )
