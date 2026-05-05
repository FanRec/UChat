from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import threading
import time
import tomllib
import wave
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.tts_bridge.audio_playback import AudioPlayback, playback_timeout_ms
from services.tts_bridge.lipsync_bridge_client import LipsyncBridgeClient
from services.tts_bridge.models import (
    AdaptiveTraceState,
    AudioAsset,
    BridgeTask,
    DefaultPreset,
    DiagnosticCandidate,
    DiagnosticResult,
    GenerationSession,
    LipsyncBridgeConfig,
    PlaybackProgressSnapshot,
    SubtitleSyncConfig,
    StreamStrategy,
    StreamingConfig,
    StreamingProfile,
    VendorConfig,
)
from services.tts_bridge.playback_coordinator import PlaybackCoordinator
from services.tts_bridge.sliding_window import SlidingWindowState
from services.tts_bridge.subtitle_sync import SubtitleSyncEmitter
from services.tts_bridge.synthesis_scheduler import SynthesisScheduler
from services.tts_bridge.vendor_runtime import VendorRuntime

class TTSBridgeConfigError(RuntimeError):
    pass


class StreamingSynthesisError(RuntimeError):
    def __init__(self, detail: str, *, status_code: int, fallback_allowed: bool) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.fallback_allowed = fallback_allowed


@dataclass(frozen=True)
class TTSBridgeConfig:
    service_name: str
    base_url: str
    host: str
    port: int
    vendor_port: int
    startup_timeout_ms: int
    request_timeout_ms: int
    output_dir: Path
    playback_enabled: bool
    vendor: VendorConfig
    preset: DefaultPreset
    streaming: StreamingConfig
    subtitle_sync: SubtitleSyncConfig = SubtitleSyncConfig()
    lipsync_bridge: LipsyncBridgeConfig = LipsyncBridgeConfig()

    @classmethod
    def load(cls, path: str | Path) -> "TTSBridgeConfig":
        config_path = Path(path)
        if not config_path.exists():
            raise TTSBridgeConfigError(f"config file not found: {config_path}")
        repo_root = config_path.resolve().parents[3]
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)

        service_name = str(raw.get("service_name", "tts_bridge")).strip() or "tts_bridge"
        base_url = str(raw.get("base_url", "http://127.0.0.1:8102")).strip().rstrip("/")
        host = str(raw.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        port = int(raw.get("port", 8102))
        vendor_port = int(raw.get("vendor_port", 9880))
        startup_timeout_ms = int(raw.get("startup_timeout_ms", 30000))
        request_timeout_ms = int(raw.get("request_timeout_ms", 3000))
        output_dir = _resolve_path(repo_root, Path(str(raw.get("output_dir", "services/tts_bridge/output"))))
        playback_enabled = bool(raw.get("playback_enabled", False))

        vendor_raw = raw.get("vendor", {})
        preset_raw = raw.get("preset", {})
        if not isinstance(vendor_raw, dict):
            raise TTSBridgeConfigError("invalid [vendor] section")
        if not isinstance(preset_raw, dict):
            raise TTSBridgeConfigError("invalid [preset] section")

        vendor = VendorConfig(
            api_style=str(vendor_raw.get("api_style", "api_v2")).strip() or "api_v2",
            python_executable=str(
                _resolve_path(repo_root, Path(str(vendor_raw.get("python_executable", sys.executable))))
            ).strip()
            or sys.executable,
            entry_script=_resolve_path(
                repo_root,
                Path(str(vendor_raw.get("entry_script", "services/tts_bridge/vendor/gpt_sovits_v2pro/api_v2.py"))),
            ),
            tts_config_path=_optional_path(
                repo_root,
                str(
                    vendor_raw.get(
                        "tts_config_path",
                        "services/tts_bridge/vendor/gpt_sovits_v2pro/GPT_SoVITS/configs/tts_infer.yaml",
                    )
                ),
            ),
            gpt_model_path=_optional_path(repo_root, str(vendor_raw.get("gpt_model_path", ""))),
            sovits_model_path=_optional_path(repo_root, str(vendor_raw.get("sovits_model_path", ""))),
            device=str(vendor_raw.get("device", "cpu")).strip() or "cpu",
        )
        preset = DefaultPreset(
            ref_audio_path=_optional_path(repo_root, str(preset_raw.get("ref_audio_path", ""))),
            prompt_text=str(preset_raw.get("prompt_text", "")),
            prompt_lang=str(preset_raw.get("prompt_lang", "zh")).strip() or "zh",
            text_lang=str(preset_raw.get("text_lang", "zh")).strip() or "zh",
            text_split_method=str(preset_raw.get("text_split_method", "cut5")).strip() or "cut5",
            speed_factor=float(preset_raw.get("speed_factor", 1.0)),
            media_type=str(preset_raw.get("media_type", "wav")).strip() or "wav",
        )

        streaming_raw = raw.get("streaming", {})
        if not isinstance(streaming_raw, dict):
            streaming_raw = {}
        subtitle_sync_raw = raw.get("subtitle_sync", {})
        if not isinstance(subtitle_sync_raw, dict):
            subtitle_sync_raw = {}
        lipsync_bridge_raw = raw.get("lipsync_bridge", {})
        if not isinstance(lipsync_bridge_raw, dict):
            lipsync_bridge_raw = {}
        streaming_device_raw = str(streaming_raw.get("device", "")).strip()
        stream_strategy_raw = str(streaming_raw.get("stream_strategy", "adaptive")).strip() or "adaptive"
        if stream_strategy_raw not in {"adaptive", "fixed_streaming", "fixed_batch"}:
            raise TTSBridgeConfigError(f"invalid streaming.stream_strategy: {stream_strategy_raw}")
        streaming = StreamingConfig(
            enabled=bool(streaming_raw.get("enabled", True)),
            stream_strategy=stream_strategy_raw,  # type: ignore[arg-type]
            media_type=str(streaming_raw.get("media_type", "raw")).strip() or "raw",
            sample_rate=int(streaming_raw.get("sample_rate", 32000)),
            vendor_streaming_mode=int(streaming_raw.get("vendor_streaming_mode", 2)),
            min_chunk_length=int(streaming_raw.get("min_chunk_length", 48)),
            fragment_interval=float(streaming_raw.get("fragment_interval", 0.10)),
            batch_size=int(streaming_raw.get("batch_size", 1)),
            prebuffer_ms=int(streaming_raw.get("prebuffer_ms", 260)),
            rebuffer_ms=int(streaming_raw.get("rebuffer_ms", 420)),
            drain_timeout_ms=int(streaming_raw.get("drain_timeout_ms", 2000)),
            fallback_to_batch_on_failure=bool(streaming_raw.get("fallback_to_batch_on_failure", True)),
            adaptive_playback_start_latency_ms=int(streaming_raw.get("adaptive_playback_start_latency_ms", 2000)),
            adaptive_rebuffer_threshold=int(streaming_raw.get("adaptive_rebuffer_threshold", 2)),
            adaptive_max_chunk_gap_ms=float(streaming_raw.get("adaptive_max_chunk_gap_ms", 400.0)),
            adaptive_realtime_factor_threshold=float(streaming_raw.get("adaptive_realtime_factor_threshold", 0.85)),
            adaptive_batch_recovery_successes=int(streaming_raw.get("adaptive_batch_recovery_successes", 2)),
            device=streaming_device_raw or None,
        )

        return cls(
            service_name=service_name,
            base_url=base_url,
            host=host,
            port=port,
            vendor_port=vendor_port,
            startup_timeout_ms=startup_timeout_ms,
            request_timeout_ms=request_timeout_ms,
            output_dir=output_dir,
            playback_enabled=playback_enabled,
            vendor=vendor,
            preset=preset,
            streaming=streaming,
            subtitle_sync=SubtitleSyncConfig(
                enabled=bool(subtitle_sync_raw.get("enabled", False)),
                obs_base_url=str(subtitle_sync_raw.get("obs_base_url", "http://127.0.0.1:8104")).strip().rstrip("/"),
                progress_interval_ms=int(subtitle_sync_raw.get("progress_interval_ms", 33)),
                fallback_mode=str(subtitle_sync_raw.get("fallback_mode", "sentence_only")).strip() or "sentence_only",
            ),
            lipsync_bridge=LipsyncBridgeConfig(
                enabled=bool(lipsync_bridge_raw.get("enabled", False)),
                base_url=str(lipsync_bridge_raw.get("base_url", "http://127.0.0.1:8105")).strip().rstrip("/"),
                request_timeout_ms=max(50, int(lipsync_bridge_raw.get("request_timeout_ms", 250) or 250)),
                inline_pcm_max_bytes=max(4096, int(lipsync_bridge_raw.get("inline_pcm_max_bytes", 4 * 1024 * 1024) or 4 * 1024 * 1024)),
            ),
        )


class SpeakRequest(BaseModel):
    trace_id: str
    segment_id: str
    index: int
    text: str
    kind: str = "sentence"
    timeout_ms: int = 3000
    generation_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelRequest(BaseModel):
    task_id: str
    trace_id: str


class CancelTraceRequest(BaseModel):
    trace_id: str


class TurnEndRequest(BaseModel):
    trace_id: str
    generation_id: int | None = None
    last_segment_index: int | None = None


class TTSBridgeService:
    def __init__(
        self,
        config: TTSBridgeConfig,
        *,
        vendor_manager: VendorRuntime | None = None,
        player: AudioPlayback | None = None,
    ) -> None:
        self.config = config
        self.vendor_manager = vendor_manager or VendorRuntime(config, config_error=TTSBridgeConfigError)
        self.player = player or AudioPlayback(device=config.streaming.device)
        self.subtitle_sync = SubtitleSyncEmitter(config.subtitle_sync)
        self.lipsync_bridge = LipsyncBridgeClient(config.lipsync_bridge)
        self._tasks: dict[str, BridgeTask] = {}
        self._adaptive_state_by_trace: dict[str, AdaptiveTraceState] = {}
        self._sessions_by_trace: dict[str, GenerationSession] = {}
        self._next_generation_id_by_trace: dict[str, int] = {}
        self._lock = threading.Lock()
        self._sliding_window = SlidingWindowState(lock=self._lock, sessions_by_trace=self._sessions_by_trace)
        self._scheduler = SynthesisScheduler(window_size=2)
        self._playback = PlaybackCoordinator(
            lock=self._lock,
            player=self.player,
            sessions_by_trace=self._sessions_by_trace,
            streaming_config=self.config.streaming,
        )

    def health(self) -> dict[str, Any]:
        vendor_pid = None
        if self.vendor_manager.process is not None and self.vendor_manager.process.poll() is None:
            vendor_pid = self.vendor_manager.process.pid
        vendor_ready = self.vendor_manager.ready()
        return {
            "service_ready": True,
            "vendor_ready": vendor_ready,
            "vendor_pid": vendor_pid,
            "player_ready": self.player.ready and self.config.playback_enabled,
            "playback_enabled": self.config.playback_enabled,
            "streaming_enabled": self.config.streaming.enabled,
            "subtitle_sync_enabled": self.config.subtitle_sync.enabled,
            "lipsync_bridge_enabled": self.config.lipsync_bridge.enabled,
            "is_playing": self.player.is_playing,
            "is_streaming": self.player.is_streaming,
            "active_playback_task_id": self._active_playback_task_id(),
        }

    def start(self) -> None:
        self.vendor_manager.ensure_running()
        if self.config.playback_enabled:
            self.player.start()

    def startup(self) -> None:
        self.start()
        self.vendor_manager.wait_until_ready(self.config.startup_timeout_ms)

    def close(self) -> None:
        self.player.stop()
        self.subtitle_sync.close()
        self.lipsync_bridge.close()
        self.vendor_manager.close()

    def speak(self, request: SpeakRequest) -> dict[str, Any]:
        if not request.text.strip():
            raise HTTPException(status_code=400, detail="text must not be empty")
        preset = self.config.preset
        if not preset.ref_audio_path:
            raise HTTPException(status_code=500, detail="tts preset ref_audio_path is not configured")
        ref_audio_path = preset.ref_audio_path.resolve()
        if not ref_audio_path.exists():
            raise HTTPException(status_code=500, detail=f"tts preset ref audio not found: {ref_audio_path}")

        streaming = self.config.streaming
        vendor_ready = self.vendor_manager.ready()
        print(f"[TTS] speak: text={request.text[:40]!r} vendor_ready={vendor_ready} streaming={streaming.enabled}", flush=True)

        self.vendor_manager.ensure_running()
        if not self.vendor_manager.wait_until_ready(self.config.startup_timeout_ms):
            print(f"[TTS] vendor NOT ready after wait", flush=True)
            raise HTTPException(status_code=503, detail="vendor tts is not ready")

        generation_id = self._resolve_generation_id(request)
        task_id = request.segment_id
        cancel_token = f"cancel_{uuid4().hex[:16]}"
        task = BridgeTask(task_id=task_id, trace_id=request.trace_id, state="pending", cancel_token=cancel_token)
        with self._lock:
            self._tasks[task_id] = task

        task.state = "in_progress"
        task.started_at = time.time()
        task.detail["text"] = request.text
        task.detail["segment_index"] = int(request.index)
        task.detail["generation_id"] = generation_id
        self._register_segment(
            trace_id=request.trace_id,
            generation_id=generation_id,
            segment_index=int(request.index),
            task_id=task_id,
            text=request.text,
            state="pending",
        )
        profile = self._streaming_profile_for_request(request.trace_id)
        task.detail["stream_profile"] = profile.label
        task.detail["stream_strategy"] = streaming.stream_strategy

        timeout = min(request.timeout_ms, self.config.request_timeout_ms)

        use_batch_only = self._should_force_batch_for_trace(request.trace_id)

        return self._deliver_task(
            task=task,
            ref_audio_path=ref_audio_path,
            timeout_ms=timeout,
            profile=profile,
            use_batch_only=use_batch_only,
        )

    def _deliver_task(
        self,
        *,
        task: BridgeTask,
        ref_audio_path: Path,
        timeout_ms: int,
        profile: StreamingProfile,
        use_batch_only: bool,
    ) -> dict[str, Any]:
        streaming = self.config.streaming
        generation_id = int(task.detail.get("generation_id", 1) or 1)
        segment_index = int(task.detail.get("segment_index", 0) or 0)
        prefer_batch_prefetch = self._scheduler.prefer_batch_prefetch(
            playback_enabled=self.config.playback_enabled,
            player_is_playing=self.player.is_playing,
        )
        self._sliding_window.wait_for_issue_window(
            trace_id=task.trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            window_size=self._scheduler.window_size + (1 if prefer_batch_prefetch else 0),
        )
        with self._lock:
            session = self._sessions_by_trace.get(task.trace_id)
            if session is not None and session.generation_id == generation_id:
                record = session.segments.get(segment_index)
                if record is not None and record.state not in {"obsolete", "cancelled"}:
                    record.state = "synthesizing"
        if self._scheduler.should_stream(
            streaming_enabled=streaming.enabled,
            stream_strategy=streaming.stream_strategy,
            use_batch_only=use_batch_only,
            prefer_batch_prefetch=prefer_batch_prefetch,
            playback_enabled=self.config.playback_enabled,
            player_supports_streaming=self.player.supports_streaming,
        ):
            try:
                return self._speak_streaming(task, ref_audio_path, timeout_ms, profile=profile)
            except StreamingSynthesisError as exc:
                if streaming.fallback_to_batch_on_failure and exc.fallback_allowed and not task.cancel_requested:
                    print(f"[TTS] streaming unavailable, fallback to batch for task {task.task_id}: {exc.detail}", flush=True)
                    return self._speak_batch(task, ref_audio_path, timeout_ms, adaptive_fallback=True)
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return self._speak_batch(
            task,
            ref_audio_path,
            timeout_ms,
            adaptive_fallback=(streaming.stream_strategy == "fixed_batch" or use_batch_only),
        )

    def _speak_streaming(self, task: BridgeTask, ref_audio_path: Path, timeout_ms: int, *, profile: StreamingProfile) -> dict[str, Any]:
        """流式合成 + 实时播放：边合成边播，最低延迟。"""
        streaming = self.config.streaming
        preset = self.config.preset
        payload: dict[str, Any] = {
            "text": task.detail["text"],
            "text_lang": preset.text_lang,
            "ref_audio_path": str(ref_audio_path),
            "prompt_text": preset.prompt_text,
            "prompt_lang": preset.prompt_lang,
            "text_split_method": preset.text_split_method,
            "speed_factor": preset.speed_factor,
            "streaming_mode": profile.vendor_streaming_mode,
            "media_type": streaming.media_type,
            "batch_size": streaming.batch_size,
            "fragment_interval": profile.fragment_interval,
            "min_chunk_length": profile.min_chunk_length,
        }

        started_at = time.perf_counter()
        audio_buffer = bytearray()
        chunk_count = 0
        chunk_gap_total_ms = 0.0
        chunk_gap_max_ms = 0.0
        chunk_gap_over_120ms = 0
        chunk_audio_total_ms = 0.0
        last_chunk_at: float | None = None

        try:
            for chunk in self.vendor_manager.synthesize_stream(payload, timeout_ms=timeout_ms):
                if task.cancel_requested:
                    print(f"[TTS] streaming cancelled for task {task.task_id}", flush=True)
                    break
                now = time.perf_counter()
                if last_chunk_at is not None:
                    gap_ms = (now - last_chunk_at) * 1000
                    chunk_gap_total_ms += gap_ms
                    chunk_gap_max_ms = max(chunk_gap_max_ms, gap_ms)
                    if gap_ms >= 120:
                        chunk_gap_over_120ms += 1
                last_chunk_at = now
                audio_buffer.extend(chunk)
                chunk_count += 1
                chunk_audio_total_ms += len(chunk) / 2 / streaming.sample_rate * 1000
        except httpx.TimeoutException as exc:
            task.state = "failed"
            task.completed_at = time.time()
            task.error = str(exc)
            raise StreamingSynthesisError("tts vendor request timed out", status_code=504, fallback_allowed=not self._player_playback_started()) from exc
        except httpx.HTTPError as exc:
            task.state = "failed"
            task.completed_at = time.time()
            task.error = str(exc)
            raise StreamingSynthesisError(f"tts vendor request failed: {exc}", status_code=502, fallback_allowed=not self._player_playback_started()) from exc

        if task.cancel_requested:
            task.state = "cancelled"
            task.completed_at = time.time()
            return {
                "status": "cancelled",
                "task_id": task.task_id,
                "audio_id": "",
                "cancel_token": task.cancel_token,
                "audio_path": "",
                "duration_ms": 0,
                "sample_rate": streaming.sample_rate,
                "media_type": streaming.media_type,
                "playback_started": False,
                "streaming_mode": True,
            }

        total_bytes = len(audio_buffer)
        duration_ms = int(total_bytes / 2 / streaming.sample_rate * 1000) if streaming.sample_rate > 0 else 0

        avg_chunk_gap_ms = round(chunk_gap_total_ms / max(chunk_count - 1, 1), 2) if chunk_count > 1 else 0.0
        avg_chunk_audio_ms = round(chunk_audio_total_ms / max(chunk_count, 1), 2) if chunk_count > 0 else 0.0
        if total_bytes <= 0:
            task.state = "failed"
            task.completed_at = time.time()
            task.error = "tts vendor returned empty audio"
            print(f"[TTS] streaming vendor returned empty audio", flush=True)
            raise StreamingSynthesisError("tts vendor returned empty audio", status_code=502, fallback_allowed=True)

        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        realtime_factor = round(duration_ms / latency_ms, 3) if latency_ms > 0 else None
        audio_id = hashlib.sha1(str(task.task_id).encode()).hexdigest()[:16]
        generation_id = int(task.detail.get("generation_id", 1) or 1)
        segment_index = int(task.detail.get("segment_index", 0) or 0)
        detail = {
            "duration_ms": duration_ms,
            "sample_rate": streaming.sample_rate,
            "latency_ms": latency_ms,
            "total_bytes": total_bytes,
            "stream_profile": profile.label,
            "chunk_count": chunk_count,
            "first_chunk_latency_ms": round(((chunk_audio_total_ms and 0) or 0), 2),
            "playback_start_latency_ms": None,
            "underrun_count": 0,
            "rebuffer_count": 0,
            "avg_chunk_gap_ms": avg_chunk_gap_ms,
            "max_chunk_gap_ms": round(chunk_gap_max_ms, 2),
            "chunk_gap_over_120ms": chunk_gap_over_120ms,
            "avg_chunk_audio_ms": avg_chunk_audio_ms,
            "realtime_factor": realtime_factor,
            "drain_timeout_ms": max(duration_ms + 1500, self.config.streaming.drain_timeout_ms),
            "playback_completed": False,
        }
        asset = AudioAsset(
            kind="pcm",
            artifact_path=None,
            sample_rate=streaming.sample_rate,
            duration_ms=duration_ms,
            media_type=streaming.media_type,
            pcm_bytes=bytes(audio_buffer),
            metrics=dict(detail),
        )
        registered = self._register_ready_segment(
            trace_id=task.trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            task_id=task.task_id,
            text=str(task.detail.get("text", "")),
            asset=asset,
            detail=detail,
        )
        if not registered:
            task.state = "cancelled"
            task.completed_at = time.time()
            return {
                "status": "cancelled",
                "task_id": task.task_id,
                "audio_id": "",
                "cancel_token": task.cancel_token,
                "audio_path": "",
                "duration_ms": 0,
                "sample_rate": streaming.sample_rate,
                "media_type": streaming.media_type,
                "playback_started": False,
                "playback_completed": False,
                "streaming_mode": True,
                "late_result_discarded": True,
            }
        playback_result = self._play_ready_segment(trace_id=task.trace_id, generation_id=generation_id, segment_index=segment_index)
        playback_result = self._playback.finalize_late_result(
            is_current_generation=self._sliding_window.is_current_generation(trace_id=task.trace_id, generation_id=generation_id),
            playback_result=playback_result,
        )
        first_chunk_latency_ms = playback_result.get("first_chunk_latency_ms")
        playback_start_latency_ms = playback_result.get("playback_start_latency_ms")
        underrun_count = int(playback_result.get("underrun_count", 0) or 0)
        rebuffer_count = int(playback_result.get("rebuffer_count", 0) or 0)
        task.state = "completed"
        task.completed_at = time.time()
        task.audio_id = audio_id
        task.detail = {
            **task.detail,
            **detail,
        }
        task.detail["playback_started"] = playback_result["playback_started"]
        task.detail["playback_completed"] = playback_result["playback_completed"]
        task.detail["first_chunk_latency_ms"] = first_chunk_latency_ms
        task.detail["playback_start_latency_ms"] = playback_start_latency_ms
        task.detail["underrun_count"] = underrun_count
        task.detail["rebuffer_count"] = rebuffer_count
        self._mark_segment_completed(
            trace_id=task.trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            detail=task.detail,
        )
        self._record_adaptive_result(task.trace_id, task.detail, used_batch=False)
        print(
            "[TTS] streamed: "
            f"{duration_ms}ms audio in {latency_ms}ms "
            f"({total_bytes} bytes, chunks={chunk_count}, first_chunk={first_chunk_latency_ms}, "
            f"playback_start={playback_start_latency_ms}, underrun={underrun_count}, "
            f"rebuffer={rebuffer_count}, avg_gap={avg_chunk_gap_ms}ms, "
            f"max_gap={round(chunk_gap_max_ms, 2)}ms, avg_chunk_audio={avg_chunk_audio_ms}ms, "
            f"rtf={realtime_factor})",
            flush=True,
        )

        return {
            "status": "delivered",
            "task_id": task.task_id,
            "audio_id": audio_id,
            "cancel_token": task.cancel_token,
            "audio_path": "",
            "duration_ms": duration_ms,
            "sample_rate": streaming.sample_rate,
            "media_type": streaming.media_type,
            "stream_profile": profile.label,
            "playback_started": playback_result["playback_started"],
            "playback_completed": playback_result["playback_completed"],
            "first_chunk_latency_ms": first_chunk_latency_ms,
            "playback_start_latency_ms": playback_start_latency_ms,
            "chunk_count": chunk_count,
            "underrun_count": underrun_count,
            "rebuffer_count": rebuffer_count,
            "avg_chunk_gap_ms": avg_chunk_gap_ms,
            "max_chunk_gap_ms": round(chunk_gap_max_ms, 2),
            "chunk_gap_over_120ms": chunk_gap_over_120ms,
            "avg_chunk_audio_ms": avg_chunk_audio_ms,
            "realtime_factor": realtime_factor,
            "streaming_mode": True,
            "late_result_discarded": bool(playback_result.get("late_result_discarded", False)),
        }

    def _speak_batch(self, task: BridgeTask, ref_audio_path: Path, timeout_ms: int, *, adaptive_fallback: bool = False) -> dict[str, Any]:
        """传统批量合成：等完整 WAV 返回后播放。"""
        preset = self.config.preset
        payload: dict[str, Any] = {"media_type": preset.media_type}
        if self.config.vendor.api_style == "legacy":
            payload.update({"text": task.detail["text"], "text_language": preset.text_lang})
        else:
            payload.update({
                "text": task.detail["text"],
                "text_lang": preset.text_lang,
                "ref_audio_path": str(ref_audio_path),
                "prompt_text": preset.prompt_text,
                "prompt_lang": preset.prompt_lang,
                "text_split_method": preset.text_split_method,
                "speed_factor": preset.speed_factor,
                "streaming_mode": False,
            })

        synth = self._synthesize_batch_audio(task, payload=payload, timeout_ms=timeout_ms, media_type=preset.media_type)
        return self._play_batch_artifact(task, synth=synth, adaptive_fallback=adaptive_fallback)

    def _synthesize_batch_audio(
        self,
        task: BridgeTask,
        *,
        payload: dict[str, Any],
        timeout_ms: int,
        media_type: str,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        try:
            response = self.vendor_manager.synthesize(payload, timeout_ms=timeout_ms)
        except httpx.TimeoutException as exc:
            task.state = "failed"
            task.completed_at = time.time()
            task.error = str(exc)
            raise HTTPException(status_code=504, detail="tts vendor request timed out") from exc
        except httpx.HTTPError as exc:
            task.state = "failed"
            task.completed_at = time.time()
            task.error = str(exc)
            raise HTTPException(status_code=502, detail=f"tts vendor request failed: {exc}") from exc

        if response.status_code != 200:
            task.state = "failed"
            task.completed_at = time.time()
            task.error = response.text
            print(f"[TTS] vendor error {response.status_code}: {response.text[:500]}", flush=True)
            raise HTTPException(status_code=502, detail=self._vendor_failure_detail(response))

        audio_bytes = response.content
        duration_ms, sample_rate = _wav_metadata(audio_bytes)
        if sample_rate is None or duration_ms is None or duration_ms <= 0:
            task.state = "failed"
            task.completed_at = time.time()
            task.error = "tts vendor returned empty audio"
            print(f"[TTS] vendor returned empty audio: {len(audio_bytes)} bytes", flush=True)
            raise HTTPException(status_code=502, detail="tts vendor returned empty audio")

        audio_id = hashlib.sha1(audio_bytes).hexdigest()[:16]
        artifact_path = self._write_audio_artifact(
            task_id=task.task_id, audio_id=audio_id, audio_bytes=audio_bytes, media_type=media_type,
        )
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        print(f"[TTS] delivered: {duration_ms}ms audio in {latency_ms}ms ({len(audio_bytes)} bytes)", flush=True)
        return {
            "audio_id": audio_id,
            "artifact_path": artifact_path,
            "duration_ms": duration_ms,
            "sample_rate": sample_rate,
            "latency_ms": latency_ms,
        }

    def _play_batch_artifact(
        self,
        task: BridgeTask,
        *,
        synth: dict[str, Any],
        adaptive_fallback: bool,
    ) -> dict[str, Any]:
        asset = AudioAsset(
            kind="file",
            artifact_path=Path(str(synth["artifact_path"])),
            sample_rate=int(synth["sample_rate"]),
            duration_ms=int(synth["duration_ms"]),
            media_type=self.config.preset.media_type,
            metrics={"batch_mode": True, "adaptive_fallback": adaptive_fallback},
        )
        generation_id = int(task.detail.get("generation_id", 1) or 1)
        segment_index = int(task.detail.get("segment_index", 0) or 0)
        registered = self._register_ready_segment(
            trace_id=task.trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            task_id=task.task_id,
            text=str(task.detail.get("text", "")),
            asset=asset,
            detail={
                "duration_ms": synth["duration_ms"],
                "sample_rate": synth["sample_rate"],
                "latency_ms": synth["latency_ms"],
                "batch_mode": True,
                "adaptive_fallback": adaptive_fallback,
            },
        )
        if not registered:
            task.state = "cancelled"
            task.completed_at = time.time()
            return {
                "status": "cancelled",
                "task_id": task.task_id,
                "audio_id": "",
                "cancel_token": task.cancel_token,
                "audio_path": "",
                "duration_ms": 0,
                "sample_rate": int(synth["sample_rate"]),
                "media_type": self.config.preset.media_type,
                "playback_started": False,
                "playback_completed": False,
                "batch_mode": True,
                "adaptive_fallback": adaptive_fallback,
                "late_result_discarded": True,
            }
        playback_result = self._play_ready_segment(trace_id=task.trace_id, generation_id=generation_id, segment_index=segment_index)
        playback_result = self._playback.finalize_late_result(
            is_current_generation=self._sliding_window.is_current_generation(trace_id=task.trace_id, generation_id=generation_id),
            playback_result=playback_result,
        )
        task.state = "completed"
        task.completed_at = time.time()
        task.audio_id = str(synth["audio_id"])
        task.artifact_path = str(synth["artifact_path"])
        task.detail = {
            **task.detail,
            "duration_ms": synth["duration_ms"],
            "sample_rate": synth["sample_rate"],
            "latency_ms": synth["latency_ms"],
            "batch_mode": True,
            "adaptive_fallback": adaptive_fallback,
            "playback_started": playback_result["playback_started"],
            "playback_completed": playback_result["playback_completed"],
            "late_result_discarded": bool(playback_result.get("late_result_discarded", False)),
        }
        self._record_adaptive_result(task.trace_id, task.detail, used_batch=True)
        self._mark_segment_completed(
            trace_id=task.trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            detail=task.detail,
        )

        return {
            "status": "delivered",
            "task_id": task.task_id,
            "audio_id": synth["audio_id"],
            "cancel_token": task.cancel_token,
            "audio_path": str(synth["artifact_path"]),
            "duration_ms": synth["duration_ms"],
            "sample_rate": synth["sample_rate"],
            "media_type": self.config.preset.media_type,
            "playback_started": playback_result["playback_started"],
            "playback_completed": playback_result["playback_completed"],
            "batch_mode": True,
            "adaptive_fallback": adaptive_fallback,
            "late_result_discarded": bool(playback_result.get("late_result_discarded", False)),
        }

    def _play_streaming_asset(self, *, task: BridgeTask, asset: AudioAsset, chunk_buffer: list[bytes]) -> dict[str, Any]:
        if not callable(getattr(self.player, "progress_snapshot", None)):
            playback_completed = self.player.play_pcm_and_wait(
                pcm_bytes=asset.pcm_bytes or b"",
                sample_rate=asset.sample_rate,
                timeout_ms=playback_timeout_ms(asset.duration_ms, self.config.streaming.drain_timeout_ms),
            )
            stats = self.player.stream_stats()
            snapshot = self._run_subtitle_progress_loop(task=task, duration_ms=asset.duration_ms)
            return {
                **stats,
                "playback_started": bool(stats.get("playback_started", False)),
                "playback_completed": playback_completed,
                "played_ms": snapshot.played_ms,
                "buffered_ms": snapshot.buffered_ms,
            }
        timeout_ms = int(asset.metrics.get("drain_timeout_ms") or playback_timeout_ms(asset.duration_ms, self.config.streaming.drain_timeout_ms))
        self.player.start_stream(
            sample_rate=asset.sample_rate,
            prebuffer_ms=self.config.streaming.prebuffer_ms,
            rebuffer_ms=self.config.streaming.rebuffer_ms,
        )
        for chunk in chunk_buffer:
            if task.cancel_requested:
                break
            self.player.feed(chunk)
        self.player.finish_stream()
        snapshot_box: dict[str, PlaybackProgressSnapshot] = {}
        progress_thread = threading.Thread(
            target=lambda: snapshot_box.setdefault("snapshot", self._run_subtitle_progress_loop(task=task, duration_ms=asset.duration_ms)),
            daemon=True,
        )
        progress_thread.start()
        playback_completed = self.player.wait_until_drained(timeout_ms)
        progress_thread.join(timeout=1.0)
        snapshot = snapshot_box.get("snapshot", self._player_progress_snapshot(duration_ms=asset.duration_ms))
        stats = self.player.stream_stats()
        self.player.stop_stream()
        return {
            **stats,
            "playback_started": bool(stats.get("playback_started", False)),
            "playback_completed": playback_completed,
            "played_ms": snapshot.played_ms,
            "buffered_ms": snapshot.buffered_ms,
        }

    def _play_file_asset(self, *, task: BridgeTask, asset: AudioAsset) -> dict[str, Any]:
        if asset.artifact_path is None:
            return {"playback_started": False, "playback_completed": False}
        if not callable(getattr(self.player, "progress_snapshot", None)):
            timeout_ms = playback_timeout_ms(asset.duration_ms, self.config.streaming.drain_timeout_ms)
            playback_completed = self.player.enqueue_and_wait(asset.artifact_path, wait=True, timeout_ms=timeout_ms)
            snapshot = self._run_subtitle_progress_loop(task=task, duration_ms=asset.duration_ms)
            return {
                "playback_started": snapshot.playback_started,
                "playback_completed": playback_completed,
                "played_ms": snapshot.played_ms,
                "buffered_ms": snapshot.buffered_ms,
            }
        timeout_ms = playback_timeout_ms(asset.duration_ms, self.config.streaming.drain_timeout_ms)
        snapshot_box: dict[str, PlaybackProgressSnapshot] = {}
        progress_thread = threading.Thread(
            target=lambda: snapshot_box.setdefault("snapshot", self._run_subtitle_progress_loop(task=task, duration_ms=asset.duration_ms)),
            daemon=True,
        )
        progress_thread.start()
        playback_completed = self.player.play_file_as_pcm_and_wait(
            path=asset.artifact_path,
            timeout_ms=timeout_ms,
            prebuffer_ms=min(self.config.streaming.prebuffer_ms, 80),
            rebuffer_ms=min(self.config.streaming.rebuffer_ms, 160),
        )
        progress_thread.join(timeout=1.0)
        snapshot = snapshot_box.get("snapshot", self._player_progress_snapshot(duration_ms=asset.duration_ms))
        self.player.stop_stream()
        return {
            "playback_started": snapshot.playback_started,
            "playback_completed": playback_completed,
            "played_ms": snapshot.played_ms,
            "buffered_ms": snapshot.buffered_ms,
        }

    def _play_batch_asset(self, *, task: BridgeTask, asset: AudioAsset) -> dict[str, Any]:
        return self._play_file_asset(task=task, asset=asset)

    def _run_subtitle_progress_loop(self, *, task: BridgeTask, duration_ms: int) -> PlaybackProgressSnapshot:
        generation_id = int(task.detail.get("generation_id", 1) or 1)
        segment_index = int(task.detail.get("segment_index", 0) or 0)
        progress_snapshot = getattr(self.player, "progress_snapshot", None)
        if not callable(progress_snapshot):
            subtitle_sync_config = getattr(self.subtitle_sync, "config", None)
            fallback_mode = getattr(subtitle_sync_config, "fallback_mode", "sentence_only")
            if fallback_mode == "sentence_only":
                snapshot = self.subtitle_sync.emit_estimated_progress(
                    trace_id=task.trace_id,
                    generation_id=generation_id,
                    segment_index=segment_index,
                    task_id=task.task_id,
                    text=str(task.detail.get("text", "")),
                    duration_ms=duration_ms,
                    cancel_requested=lambda: task.cancel_requested,
                )
            else:
                snapshot = self._player_progress_snapshot(duration_ms=duration_ms)
        else:
            snapshot = self.subtitle_sync.run_progress_loop(
                trace_id=task.trace_id,
                generation_id=generation_id,
                segment_index=segment_index,
                task_id=task.task_id,
                text=str(task.detail.get("text", "")),
                duration_ms=duration_ms,
                snapshot_getter=lambda: progress_snapshot(duration_ms=duration_ms),
                cancel_requested=lambda: task.cancel_requested,
            )
        task.subtitle_state = {
            "generation_id": generation_id,
            "segment_index": segment_index,
            "text": str(task.detail.get("text", "")),
            "played_ms": snapshot.played_ms,
            "playback_started": snapshot.playback_started,
            "playback_completed": snapshot.segment_finished,
        }
        with self._lock:
            session = self._sessions_by_trace.get(task.trace_id)
            if session is not None and session.generation_id == generation_id:
                if session.expected_end_index == segment_index and snapshot.segment_finished:
                    self.subtitle_sync.emit_turn_end(
                        trace_id=task.trace_id,
                        generation_id=generation_id,
                        task_id=task.task_id,
                        segment_index=segment_index,
                    )
        return snapshot

    def _player_progress_snapshot(self, *, duration_ms: int) -> PlaybackProgressSnapshot:
        progress_snapshot = getattr(self.player, "progress_snapshot", None)
        if callable(progress_snapshot):
            return progress_snapshot(duration_ms=duration_ms)
        stats = {}
        stream_stats = getattr(self.player, "stream_stats", None)
        if callable(stream_stats):
            stats = dict(stream_stats() or {})
        return PlaybackProgressSnapshot(
            playback_started=bool(stats.get("playback_started", False)),
            played_ms=float(duration_ms if stats.get("playback_started", False) else 0.0),
            played_samples=0,
            buffered_ms=0.0,
            segment_finished=True,
            duration_ms=duration_ms,
        )

    def _player_playback_started(self) -> bool:
        stream_stats = getattr(self.player, "stream_stats", None)
        if callable(stream_stats):
            stats = dict(stream_stats() or {})
            return bool(stats.get("playback_started", False))
        return False

    def cancel(self, request: CancelRequest) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(request.task_id)
            if task is None or task.trace_id != request.trace_id:
                return {
                    "status": "not_found",
                    "task_id": request.task_id,
                    "cancel_token": "",
                }
            if task.state in {"completed", "failed"}:
                return {
                    "status": "too_late",
                    "task_id": task.task_id,
                    "cancel_token": task.cancel_token,
                }
            task.cancel_requested = True
            # 立即停止播放（流式和文件模式）
            self.player.cancel_all()
            if task.state == "pending":
                task.state = "cancelled"
                task.completed_at = time.time()
            return {
                "status": "cancelled",
                "task_id": task.task_id,
                "cancel_token": task.cancel_token,
            }

    def cancel_trace(self, request: CancelTraceRequest) -> dict[str, Any]:
        affected: list[BridgeTask]
        with self._lock:
            affected = [task for task in self._tasks.values() if task.trace_id == request.trace_id and task.state not in {"completed", "failed", "cancelled", "obsolete"}]
            for task in affected:
                task.cancel_requested = True
                if task.state == "pending":
                    task.state = "cancelled"
                    task.completed_at = time.time()
                elif task.state in {"ready", "synthesizing"}:
                    task.state = "obsolete"
                    task.completed_at = time.time()
            self._next_generation_id_by_trace[request.trace_id] = self._next_generation_id_by_trace.get(request.trace_id, 1) + 1
            generation_id = self._next_generation_id_by_trace[request.trace_id]
        self._sliding_window.close_trace(request.trace_id)
        self.player.cancel_all()
        self.subtitle_sync.emit_clear(trace_id=request.trace_id, generation_id=generation_id, reason="cancel_trace")
        self.lipsync_bridge.cancel_trace_async(trace_id=request.trace_id)
        return {
            "status": "cancelled",
            "trace_id": request.trace_id,
            "cancelled_task_count": len(affected),
        }

    def mark_turn_end(self, request: TurnEndRequest) -> dict[str, Any]:
        with self._lock:
            session = self._sessions_by_trace.get(request.trace_id)
            if session is None:
                return {"status": "not_found", "trace_id": request.trace_id}
            if request.generation_id is not None and session.generation_id != int(request.generation_id):
                return {"status": "stale", "trace_id": request.trace_id, "generation_id": session.generation_id}
            if request.last_segment_index is not None:
                session.expected_end_index = max(int(request.last_segment_index), 1)
                if session.condition is not None:
                    session.condition.notify_all()
        self.lipsync_bridge.turn_end_async(
            trace_id=request.trace_id,
            generation_id=request.generation_id,
            last_segment_index=request.last_segment_index,
        )
        return {"status": "accepted", "trace_id": request.trace_id}

    def subtitle_state(self, trace_id: str) -> dict[str, Any]:
        with self._lock:
            tasks = [task for task in self._tasks.values() if task.trace_id == trace_id and task.subtitle_state]
        tasks.sort(key=lambda item: item.subtitle_state.get("segment_index", 0))
        return {
            "trace_id": trace_id,
            "segments": [task.subtitle_state for task in tasks],
        }

    def diagnose_streaming(self, *, text: str, trace_id: str = "diag_streaming") -> dict[str, Any]:
        candidates = [
            DiagnosticCandidate(label="mode3_chunk16_gap0.04", vendor_streaming_mode=3, min_chunk_length=16, fragment_interval=0.04),
            DiagnosticCandidate(label="mode2_chunk32_gap0.08", vendor_streaming_mode=2, min_chunk_length=32, fragment_interval=0.08),
            DiagnosticCandidate(label="mode2_chunk48_gap0.10", vendor_streaming_mode=2, min_chunk_length=48, fragment_interval=0.10),
            DiagnosticCandidate(label="mode2_chunk64_gap0.12", vendor_streaming_mode=2, min_chunk_length=64, fragment_interval=0.12),
        ]
        results: list[DiagnosticResult] = []
        base_request = SpeakRequest(
            trace_id=trace_id,
            segment_id=f"diag_{uuid4().hex[:8]}",
            index=1,
            text=text,
            kind="sentence",
            timeout_ms=self.config.request_timeout_ms,
            metadata={"diagnostic": True},
        )
        for candidate in candidates:
            profile = StreamingProfile(
                label=candidate.label,
                vendor_streaming_mode=candidate.vendor_streaming_mode,
                min_chunk_length=candidate.min_chunk_length,
                fragment_interval=candidate.fragment_interval,
                prebuffer_ms=self.config.streaming.prebuffer_ms,
                rebuffer_ms=self.config.streaming.rebuffer_ms,
            )
            request = base_request.model_copy(update={"segment_id": f"diag_{candidate.label}_{uuid4().hex[:6]}"})
            result = self._run_diagnostic_candidate(request=request, profile=profile)
            results.append(DiagnosticResult(label=candidate.label, metrics=result))

        ranked = sorted(results, key=self._diagnostic_sort_key)
        recommended = ranked[0] if ranked else None
        payload = {
            "trace_id": trace_id,
            "text": text,
            "results": [{"label": item.label, "metrics": item.metrics} for item in ranked],
            "recommended": {
                "label": recommended.label,
                "metrics": recommended.metrics,
            } if recommended is not None else None,
        }
        output_dir = self.config.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "streaming_diagnostic_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def _streaming_profile_for_request(self, trace_id: str) -> StreamingProfile:
        streaming = self.config.streaming
        default_profile = StreamingProfile(
            label="continuity_default",
            vendor_streaming_mode=streaming.vendor_streaming_mode,
            min_chunk_length=streaming.min_chunk_length,
            fragment_interval=streaming.fragment_interval,
            prebuffer_ms=streaming.prebuffer_ms,
            rebuffer_ms=streaming.rebuffer_ms,
        )
        if streaming.stream_strategy != "adaptive":
            return default_profile
        state = self._adaptive_state_by_trace.get(trace_id)
        if state is not None and state.force_batch:
            return default_profile
        return default_profile

    def _resolve_generation_id(self, request: SpeakRequest) -> int:
        explicit = request.generation_id
        if explicit is None:
            explicit = request.metadata.get("generation_id")
        if explicit is not None:
            generation_id = max(int(explicit), 1)
        else:
            generation_id = 1
            with self._lock:
                if request.trace_id in self._next_generation_id_by_trace:
                    generation_id = self._next_generation_id_by_trace[request.trace_id]
                else:
                    self._next_generation_id_by_trace[request.trace_id] = generation_id
        with self._lock:
            self._next_generation_id_by_trace[request.trace_id] = max(self._next_generation_id_by_trace.get(request.trace_id, 1), generation_id)
            session = self._sessions_by_trace.get(request.trace_id)
            if session is None or session.generation_id != generation_id:
                self._sessions_by_trace[request.trace_id] = GenerationSession(
                    trace_id=request.trace_id,
                    generation_id=generation_id,
                    condition=threading.Condition(self._lock),
                )
        return generation_id

    def _register_segment(
        self,
        *,
        trace_id: str,
        generation_id: int,
        segment_index: int,
        task_id: str,
        text: str,
        state,
    ):
        return self._sliding_window.register_segment(
            trace_id=trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            task_id=task_id,
            text=text,
            state=state,
        )

    def _notify_session(self, trace_id: str) -> None:
        self._sliding_window.notify_trace(trace_id)

    def _record_adaptive_result(self, trace_id: str, metrics: dict[str, Any], *, used_batch: bool) -> None:
        if self.config.streaming.stream_strategy != "adaptive":
            return
        state = self._adaptive_state_by_trace.setdefault(trace_id, AdaptiveTraceState())
        if used_batch:
            state.consecutive_batch_successes += 1
            if state.consecutive_batch_successes >= self.config.streaming.adaptive_batch_recovery_successes:
                state.force_batch = False
                state.consecutive_batch_successes = 0
            else:
                state.force_batch = True
            return
        state.consecutive_batch_successes = 0
        playback_start_latency_ms = float(metrics.get("playback_start_latency_ms") or 0.0)
        rebuffer_count = int(metrics.get("rebuffer_count", 0) or 0)
        max_chunk_gap_ms = float(metrics.get("max_chunk_gap_ms") or 0.0)
        realtime_factor = float(metrics.get("realtime_factor") or 0.0)
        should_force_batch = (
            playback_start_latency_ms > self.config.streaming.adaptive_playback_start_latency_ms
            or rebuffer_count >= self.config.streaming.adaptive_rebuffer_threshold
            or max_chunk_gap_ms >= self.config.streaming.adaptive_max_chunk_gap_ms
            or realtime_factor < self.config.streaming.adaptive_realtime_factor_threshold
        )
        state.force_batch = should_force_batch

    def _should_force_batch_for_trace(self, trace_id: str) -> bool:
        if self.config.streaming.stream_strategy != "adaptive":
            return False
        state = self._adaptive_state_by_trace.get(trace_id)
        return bool(state is not None and state.force_batch)

    def _register_ready_segment(
        self,
        *,
        trace_id: str,
        generation_id: int,
        segment_index: int,
        task_id: str,
        text: str,
        asset: AudioAsset,
        detail: dict[str, Any],
    ) -> bool:
        return self._sliding_window.register_ready_segment(
            trace_id=trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            task_id=task_id,
            text=text,
            asset=asset,
            detail=detail,
        )

    def _play_ready_segment(self, *, trace_id: str, generation_id: int, segment_index: int) -> dict[str, Any]:
        if not self.config.playback_enabled:
            return {"playback_started": False, "playback_completed": False}
        return self._playback.play_ready_segment(
            trace_id=trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            playback_runner=lambda asset: self._play_asset_with_subtitles(
                trace_id=trace_id,
                generation_id=generation_id,
                segment_index=segment_index,
                asset=asset,
            ),
        )

    def _mark_segment_completed(self, *, trace_id: str, generation_id: int, segment_index: int, detail: dict[str, Any]) -> None:
        self._sliding_window.mark_segment_completed(
            trace_id=trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            detail=detail,
        )

    def _play_asset_with_subtitles(
        self,
        *,
        trace_id: str,
        generation_id: int,
        segment_index: int,
        asset: AudioAsset,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._sessions_by_trace.get(trace_id)
            if session is None or session.generation_id != generation_id:
                return {"playback_started": False, "playback_completed": False}
            record = session.segments.get(segment_index)
            if record is None:
                return {"playback_started": False, "playback_completed": False}
            task = self._tasks.get(record.task_id)
        if task is None:
            return {"playback_started": False, "playback_completed": False}
        self.lipsync_bridge.mirror_asset_async(
            trace_id=trace_id,
            generation_id=generation_id,
            segment_index=segment_index,
            task_id=task.task_id,
            text=str(task.detail.get("text", "")),
            asset=asset,
        )
        if asset.kind == "pcm":
            return self._play_streaming_asset(task=task, asset=asset, chunk_buffer=[asset.pcm_bytes or b""])
        return self._play_batch_asset(task=task, asset=asset)

    def _run_diagnostic_candidate(self, *, request: SpeakRequest, profile: StreamingProfile) -> dict[str, Any]:
        preset = self.config.preset
        if not preset.ref_audio_path:
            raise TTSBridgeConfigError("tts preset ref_audio_path is not configured")
        ref_audio_path = preset.ref_audio_path.resolve()
        task = BridgeTask(
            task_id=request.segment_id,
            trace_id=request.trace_id,
            state="pending",
            cancel_token=f"cancel_{uuid4().hex[:16]}",
        )
        task.state = "in_progress"
        task.started_at = time.time()
        task.detail["text"] = request.text
        task.detail["segment_index"] = int(request.index)
        timeout = min(request.timeout_ms, self.config.request_timeout_ms)
        return self._speak_streaming(task, ref_audio_path, timeout, profile=profile)

    def _diagnostic_sort_key(self, result: DiagnosticResult) -> tuple[int, int, float, float, float]:
        metrics = result.metrics
        playback_start_latency_ms = float(metrics.get("playback_start_latency_ms") or 0.0)
        rebuffer_count = int(metrics.get("rebuffer_count", 9999) or 9999)
        max_chunk_gap_ms = float(metrics.get("max_chunk_gap_ms") or 999999.0)
        realtime_factor = float(metrics.get("realtime_factor") or 0.0)
        meets_latency = 0 if playback_start_latency_ms <= 2000 else 1
        return (
            meets_latency,
            rebuffer_count,
            max_chunk_gap_ms,
            -realtime_factor,
            playback_start_latency_ms,
        )

    def _write_audio_artifact(self, *, task_id: str, audio_id: str, audio_bytes: bytes, media_type: str) -> Path:
        output_dir = self.config.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = output_dir / f"{task_id}_{audio_id}.{media_type}"
        artifact_path.write_bytes(audio_bytes)
        return artifact_path

    def _active_playback_task_id(self) -> str | None:
        with self._lock:
            for task in self._tasks.values():
                if task.state == "in_progress":
                    return task.task_id
        return None

    def _vendor_failure_detail(
        self,
        response: httpx.Response | None,
        body: bytes | None = None,
        *,
        status_code: int | None = None,
    ) -> str:
        if response is not None:
            text = (body if body is not None else response.content).decode("utf-8", errors="replace")
            current_status = response.status_code
        else:
            text = (body or b"").decode("utf-8", errors="replace")
            current_status = int(status_code or 500)
        detail = f"tts vendor returned {current_status}: {text}"
        lowered = text.lower()
        if "请输入有效文本" in text:
            return "vendor_text_preprocess_failed"
        if "timed out" in lowered:
            return "vendor_timeout"
        return detail


def build_app(service: TTSBridgeService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        service.startup()
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="tts_bridge", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return service.health()

    @app.post("/v1/tts/speak")
    def speak(request: SpeakRequest) -> dict[str, Any]:
        return service.speak(request)

    @app.post("/v1/tts/cancel")
    def cancel(request: CancelRequest) -> dict[str, Any]:
        return service.cancel(request)

    @app.post("/v1/tts/cancel-trace")
    def cancel_trace(request: CancelTraceRequest) -> dict[str, Any]:
        return service.cancel_trace(request)

    @app.post("/v1/tts/turn-end")
    def mark_turn_end(request: TurnEndRequest) -> dict[str, Any]:
        return service.mark_turn_end(request)

    @app.get("/v1/tts/subtitle-state")
    def subtitle_state(trace_id: str) -> dict[str, Any]:
        return service.subtitle_state(trace_id)

    return app


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "service.toml"


def build_service(config_path: str | Path | None = None) -> TTSBridgeService:
    config = TTSBridgeConfig.load(config_path or default_config_path())
    return TTSBridgeService(config)


def run_server(config_path: str | Path | None = None) -> None:
    config = TTSBridgeConfig.load(config_path or default_config_path())
    app = build_app(TTSBridgeService(config))
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


def run_streaming_diagnostic(config_path: str | Path | None = None, *, text: str) -> dict[str, Any]:
    service = build_service(config_path or default_config_path())
    try:
        service.startup()
        return service.diagnose_streaming(text=text)
    finally:
        service.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UChat tts_bridge service")
    parser.add_argument("--serve", action="store_true", help="Run the bridge HTTP service")
    parser.add_argument("--config", default=str(default_config_path()), help="Path to service.toml")
    parser.add_argument("--diagnose-streaming", action="store_true", help="Run streaming diagnostic candidates and save ranking JSON")
    parser.add_argument("--text", default="你大半夜把我叫出来就是为了让我听你测试语音合成？", help="Diagnostic text for streaming benchmark")
    return parser.parse_known_args(argv)[0]


def _wav_metadata(audio_bytes: bytes) -> tuple[int | None, int | None]:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            frame_count = wav_file.getnframes()
            sample_rate = wav_file.getframerate()
            duration_ms = int((frame_count / sample_rate) * 1000) if sample_rate > 0 else None
            return duration_ms, sample_rate
    except wave.Error:
        return None, None


def _resolve_path(repo_root: Path, value: Path) -> Path:
    raw = str(value).strip()
    if raw in {"", "."}:
        return value
    return value if value.is_absolute() else (repo_root / value)


def _optional_path(repo_root: Path, raw: str) -> Path | None:
    normalized = str(raw).strip()
    if not normalized:
        return None
    return _resolve_path(repo_root, Path(normalized))

