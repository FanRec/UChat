# OBS Subtitle Module

Directory:

- [services/obs_bridge](../../services/obs_bridge)

## Responsibilities

- receive subtitle events from the runtime or `tts_bridge`
- maintain the current subtitle snapshot
- push updates to OBS Browser Source over WebSocket
- render sentence-level and progressive subtitles

## Startup

```powershell
uv run python -m services.obs_bridge.main --serve
```

## OBS Setup

Add a Browser Source in OBS:

- URL: `http://127.0.0.1:8104/overlay/`

You can also open that URL in a browser first to confirm the overlay page loads correctly.

## Current Semantics

- one sentence per line
- newer lines are usually appended at the bottom
- the previous turn is cleared before a new reply starts
- `trace_id + generation_id` manages subtitle lifecycle

## Common Debug Checks

Start with:

- `http://127.0.0.1:8104/health`
- `http://127.0.0.1:8104/overlay/`

If the overlay page loads but no subtitles appear:

1. check whether the runtime is actually emitting sentence-level subtitle events
2. check whether `tts_bridge` has subtitle sync enabled
3. check `obs_bridge` console logs or `/health`
