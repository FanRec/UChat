# Project Structure and Startup Paths

## Directory Layout

### `uchat/`

Core runtime code:

- `cli.py`
  - Main local entry point for startup, runtime construction, adapters, and optional live polling
- `runtime/`
  - Turn orchestration, scene scheduling, output lifecycle, and traces
- `adapters/`
  - Console, Bilibili, TTS, and OBS adapters
- `identity/`
  - Identity services and local identity storage
- `models/`
  - Model providers, profiles, routes, and the OpenAI-compatible client
- `config/`
  - Main config loading and structured settings objects

### `services/`

Independent service boundaries:

- `bilibili_gateway/`
  - Live-event input gateway
- `tts_bridge/`
  - TTS synthesis, playback, and subtitle sync
- `obs_bridge/`
  - OBS subtitle overlay
- `body_service/`
  - Body-performance execution sidecar
- `lipsync_bridge/`
  - Audio-mirroring lipsync sidecar
- `identity_admin/`
  - Identity governance HTTP service

### `config/`

- `app.toml`
  - Main runtime config, LTMem, debug, logging, and service endpoints
- `models.toml`
  - Model providers, profiles, and logical routes

### `prompts/`

- `prompts/<locale>/*.prompt`

### `docs/`

Public-facing documentation.

## Minimal Runtime Combinations

### Run Only the LLM Runtime

```powershell
uv run python -m uchat.cli
```

Best for checking:

- whether `.env` is ready
- whether `config/models.toml` is valid
- whether prompts and the main runtime pipeline can generate text

### Run LLM + OBS Subtitles

Terminal 1:

```powershell
uv run python -m services.obs_bridge.main --serve
```

Terminal 2:

```powershell
uv run python -m uchat.cli
```

### Run LLM + OBS + TTS

Terminal 1:

```powershell
uv run python -m services.obs_bridge.main --serve
```

Terminal 2:

```powershell
uv run python -m services.tts_bridge.main --serve
```

Terminal 3:

```powershell
uv run python -m uchat.cli
```

### Run the Live Input Pipeline

Terminal 1:

```powershell
uv run python -m services.bilibili_gateway.main serve
```

Terminal 2:

```powershell
uv run python -m uchat.cli
```

Notes:

- Whether `uchat.cli` actually consumes live input depends on `runtime.scene_kind` and `[services.platform.bilibili].url`
- Even if live input fails, the CLI keeps console mode available

### Run Body and Lipsync Sidecars

```powershell
uv run python -m services.body_service.main --serve
```

```powershell
uv run python -m services.lipsync_bridge.main --serve
```

These are sidecars. It is usually better to add them after text, subtitles, and TTS are stable.

### Run Identity Governance

```powershell
uv run python -m services.identity_admin.main serve
```

## Recommended Debugging Order

1. Confirm `uchat.cli` can return LLM text.
2. Confirm `obs_bridge` health and overlay page are reachable.
3. Confirm `tts_bridge` is ready and its resource paths are valid.
4. Add `bilibili_gateway` and observe structured events.
5. Integrate `body_service`, `lipsync_bridge`, and `identity_admin` last.

## How to Read the Public Repo

This repository is best understood as:

- a runnable runtime pipeline
- a set of independent sidecar integration examples
- an engineering skeleton for swapping models, characters, platforms, and execution layers

It is not a one-click bundled product with every private runtime condition included.
