from __future__ import annotations

import argparse
import time

import httpx


def run_smoke(*, base_url: str, trace_id: str, generation_id: int, text: str, with_progress: bool) -> None:
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=2.0, trust_env=False)
    try:
        client.post(
            "/v1/body/command",
            json={
                "command_id": f"{trace_id}_speech_plan",
                "trace_id": trace_id,
                "body_id": "hiyori_vts",
                "command_type": "speech_plan",
                "generation_id": generation_id,
                "segment_index": 1,
                "expression": "soft_smile",
                "motion": "gentle_nod",
                "intensity": 0.7,
                "sync_to_audio": True,
                "metadata": {"commit_mode": "transient"},
            },
        ).raise_for_status()
        client.post(
            "/v1/body/speech-event",
            json={
                "trace_id": trace_id,
                "task_id": f"{trace_id}_seg_1",
                "action": "segment_start",
                "generation_id": generation_id,
                "segment_index": 1,
                "text": text,
                "metadata": {"revealed_count": 0, "playback_started": True},
            },
        ).raise_for_status()
        if with_progress:
            for revealed in range(1, min(len(text), 12) + 1):
                time.sleep(0.12)
                client.post(
                    "/v1/body/speech-event",
                    json={
                        "trace_id": trace_id,
                        "task_id": f"{trace_id}_seg_1",
                        "action": "segment_progress",
                        "generation_id": generation_id,
                        "segment_index": 1,
                        "text": text,
                        "metadata": {"revealed_count": revealed, "revealed_text": text[:revealed], "playback_started": True},
                    },
                ).raise_for_status()
        else:
            time.sleep(1.4)
        client.post(
            "/v1/body/speech-event",
            json={
                "trace_id": trace_id,
                "task_id": f"{trace_id}_seg_1",
                "action": "segment_complete",
                "generation_id": generation_id,
                "segment_index": 1,
                "text": text,
                "metadata": {"revealed_count": len(text), "revealed_text": text, "playback_completed": True},
            },
        ).raise_for_status()
        client.post("/v1/body/speech-event", json={"trace_id": trace_id, "task_id": f"{trace_id}_turn_end", "action": "turn_end", "generation_id": generation_id}).raise_for_status()
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a minimal body_service smoke sequence.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8103")
    parser.add_argument("--trace-id", default="trace_body_smoke")
    parser.add_argument("--generation-id", type=int, default=1)
    parser.add_argument("--text", default="这是一条 body_service 的最小联调烟测。")
    parser.add_argument("--with-progress", action="store_true")
    args = parser.parse_args()
    run_smoke(
        base_url=args.base_url,
        trace_id=args.trace_id,
        generation_id=args.generation_id,
        text=args.text,
        with_progress=args.with_progress,
    )


if __name__ == "__main__":
    main()
