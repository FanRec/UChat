from __future__ import annotations

import base64
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from services.tts_bridge.models import AudioAsset


@dataclass(frozen=True)
class LipsyncBridgeClientConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8105"
    request_timeout_ms: int = 250
    inline_pcm_max_bytes: int = 4 * 1024 * 1024


class LipsyncBridgeClient:
    def __init__(self, config: LipsyncBridgeClientConfig, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self.client = client or httpx.Client(base_url=config.base_url.rstrip("/"), trust_env=False)

    def close(self) -> None:
        self.client.close()

    def mirror_asset_async(
        self,
        *,
        trace_id: str,
        generation_id: int,
        segment_index: int,
        task_id: str,
        text: str,
        asset: AudioAsset,
    ) -> None:
        if not self.config.enabled:
            return
        payload = {
            "trace_id": trace_id,
            "generation_id": generation_id,
            "segment_index": segment_index,
            "task_id": task_id,
            "text": text,
            "sample_rate": asset.sample_rate,
            "duration_ms": asset.duration_ms,
            "media_type": asset.media_type,
        }
        if asset.kind == "file" and asset.artifact_path is not None:
            payload["audio_path"] = str(asset.artifact_path)
        elif asset.kind == "pcm" and asset.pcm_bytes is not None:
            if len(asset.pcm_bytes) > self.config.inline_pcm_max_bytes:
                print(
                    "[LIPSYNC] skip mirror: PCM payload too large "
                    f"({len(asset.pcm_bytes)} bytes > {self.config.inline_pcm_max_bytes})",
                    flush=True,
                )
                return
            payload["pcm_base64"] = base64.b64encode(asset.pcm_bytes).decode("ascii")
        else:
            return
        self._fire_and_forget("/v1/lipsync/mirror", payload, label=f"mirror:{task_id}")

    def cancel_trace_async(self, *, trace_id: str) -> None:
        if not self.config.enabled:
            return
        self._fire_and_forget("/v1/lipsync/cancel-trace", {"trace_id": trace_id}, label=f"cancel:{trace_id}")

    def turn_end_async(self, *, trace_id: str, generation_id: int | None, last_segment_index: int | None) -> None:
        if not self.config.enabled:
            return
        self._fire_and_forget(
            "/v1/lipsync/turn-end",
            {
                "trace_id": trace_id,
                "generation_id": generation_id,
                "last_segment_index": last_segment_index,
            },
            label=f"turn_end:{trace_id}",
        )

    def _fire_and_forget(self, path: str, payload: dict[str, Any], *, label: str) -> None:
        timeout = max(self.config.request_timeout_ms / 1000, 0.05)

        def _run() -> None:
            try:
                response = self.client.post(path, json=payload, timeout=timeout)
                if response.status_code not in {200, 202}:
                    print(
                        f"[LIPSYNC] bridge request failed for {label}: "
                        f"{response.status_code} {response.text[:200]}",
                        flush=True,
                    )
            except Exception as exc:
                print(f"[LIPSYNC] bridge request error for {label}: {exc}", flush=True)

        threading.Thread(target=_run, name=f"lipsync-bridge-{label}", daemon=True).start()
