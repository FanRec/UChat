# TTS Module

Directory:

- [services/tts_bridge](../../services/tts_bridge)

## Responsibilities

- accept sentence-level TTS requests from the runtime
- communicate with the underlying vendor/runtime
- coordinate sentence synthesis and ordered playback
- synchronize playback-side subtitles
- support `cancel`, `cancel-trace`, and `turn-end`
- notify `body_service` and `lipsync_bridge` on a best-effort basis

## Key Files

- `service.py`
  - main service logic and entry
- `synthesis_scheduler.py`
  - streaming and batch strategy
- `playback_coordinator.py`
  - playback ordering
- `audio_playback.py`
  - local playback
- `subtitle_sync.py`
  - playback-side subtitle sync
- `sliding_window.py`
  - generation and segment window state
- `lipsync_bridge_client.py`
  - sidecar notification for the lipsync mirror

## Startup

```powershell
uv run python -m services.tts_bridge.main --serve
```

If you need streaming diagnostics, check the service README and its CLI options.

## Common Failure Reasons

- missing vendor/runtime
- missing model weights
- missing `ref_audio_path`
- invalid resource paths in the service config
- unavailable local audio output device

## Public Repo Notes

- model assets, reference audio, and vendor/runtime are not included
- you must prepare the resources referenced by `services/tts_bridge/config/service.toml`
- if you do not want real TTS yet, do not start this service and let the runtime fall back to console TTS

## Subtitle Sync

When subtitle sync is enabled:

- TTS playback pushes subtitle events to `obs_bridge`
- with playback progress, subtitles advance character by character
- without fine-grained progress, the current implementation falls back to an estimated progression

## Relation to Other Sidecars

- `body_service`
  - receives speaking lifecycle events but does not block TTS
- `lipsync_bridge`
  - receives mirrored audio events but mirror failure does not block main playback

That boundary is intentional: execution-side sidecars should not slow down the TTS main path.
