# Body Service Module

Directory:

- [services/body_service](../../services/body_service)

## Current Status

`body_service` is now a real sidecar, not just a placeholder skeleton.

Its job is to turn sentence-level or turn-level body intent, TTS playback lifecycle, and local idle/speaking behavior into executable body performance, while keeping implementation details isolated in an external service.

## Responsibilities

- receive body commands and speaking lifecycle events
- maintain local body state
- drive `mock` or `VTube Studio` backends
- run local idle, passive-motion, and speaking continuity layers
- support `cancel-trace`, `turn_end`, and `clear`

## Non-Responsibilities

- reply generation
- platform input protocols
- runtime reply decisions
- frame-by-frame lipsync generation

## Current Boundary

- lipsync is primarily delegated to VTS itself or to the separate `lipsync_bridge`
- `body_service` should not back-pressure the runtime or TTS main path
- high-frequency idle and tracking behavior should stay inside the local service loop

## Startup

```powershell
uv run python -m services.body_service.main --serve
```

Minimal smoke test:

```powershell
uv run python -m services.body_service.smoke
```

## Config Entry Points

- main config: `services/body_service/config/service.toml`
- profiles: `services/body_service/config/body_profiles/*.toml`

If you want real VTube Studio integration, continue with the detailed README in that service directory.
