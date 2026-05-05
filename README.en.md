# UChat Public

![icon](./README.assets/icon.png)

<p>
  <a href="./README.md">中文</a> | <a href="./README.en.md">English</a>
</p>

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.136%2B-009688?logo=fastapi&logoColor=white)
![Uvicorn](https://img.shields.io/badge/Uvicorn-0.46%2B-4051B5)
![uv](https://img.shields.io/badge/uv-managed-5C6AC4)
![Version](https://img.shields.io/badge/version-0.1.0-6C757D)

UChat is a local-first runtime project for digital characters, AI streamers, and interactive on-screen personas.

This public repository keeps the core runtime, service boundaries, and example configuration that already exist in the main project, while removing private environment parameters, generated local data, model assets, and personal login state. It is not meant to be a polished out-of-the-box product. It is an engineering skeleton you can run, extend, and replace piece by piece.

## What This Repository Includes

- `uchat/`
  - Core runtime, event orchestration, output dispatch, identity integration, and model routing
- `services/bilibili_gateway/`
  - Bilibili live input gateway that feeds structured events into the runtime
- `services/tts_bridge/`
  - Sentence-level TTS synthesis and playback service with subtitle sync
- `services/obs_bridge/`
  - Real-time OBS Browser Source subtitle service
- `services/body_service/`
  - Body-performance execution service with `mock` and `VTube Studio` backends
- `services/lipsync_bridge/`
  - Sidecar for mirroring TTS audio into a VTS-listenable virtual device
- `services/identity_admin/`
  - Local identity-governance entry point
- `config/`
  - Main runtime configuration and model routing configuration
- `prompts/`
  - Prompt templates
- `docs/`
  - Public-facing overview and module documentation

## What Is Not Included

To avoid privacy and redistribution issues, this public repository does not include:

- Private `.env`
- Private cookies, tokens, or sessions
- Local database files and generated `debug/`, `logs/`, or `data/` artifacts
- TTS model weights, reference audio, or vendor/runtime directories
- Private character prompts or private integration assets

You should prepare your own:

- LLM API key
- Bilibili login state if you want real live input
- GPT-SoVITS runtime and model assets if you want real TTS
- VTube Studio and virtual audio device setup if you want body or lipsync integration

## Requirements

- Python `3.12+`
- `uv`
- At least one working model API key

Recommended first step:

```powershell
uv sync
```

## Local Setup

### 1. Create `.env`

The repository only ships `.env.example`. Create your own local `.env`.

At minimum:

```dotenv
DEEPSEEK_API_KEY=your_api_key_here
```

If you want real Bilibili live input, also add:

```dotenv
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_BUVID3=
BILIBILI_DEDEUSERID=
```

### 2. Replace the Public Example Identity

`runtime.identity` in `config/app.toml` is only a public example. Replace it with your own character prompt.

Do not put real secrets, cookies, or private deployment settings into `config/*.toml`.

### 3. Check Service Configs as Needed

Common config entry points:

- `config/app.toml`
- `config/models.toml`
- `services/tts_bridge/config/service.toml`
- `services/obs_bridge/config/service.toml`
- `services/bilibili_gateway/config/service.toml`
- `services/body_service/config/service.toml`
- `services/lipsync_bridge/config/service.toml`
- `services/identity_admin/config/service.toml`

## Minimal Startup Paths

### Option 1: Run Only the Core Runtime

If you only want to verify prompts, model routing, and the main reply loop:

```powershell
uv run python -m uchat.cli
```

It reads:

- `config/app.toml`
- `config/models.toml`
- local `.env`

If `runtime.scene_kind = "live_stream"` in `config/app.toml` and `[services.platform.bilibili].url` is available, the CLI will also poll `bilibili_gateway`. Otherwise it stays in console-only mode.

### Option 2: Add OBS Subtitles

Terminal 1:

```powershell
uv run python -m services.obs_bridge.main --serve
```

Terminal 2:

```powershell
uv run python -m uchat.cli
```

In OBS, add a Browser Source:

- URL: `http://127.0.0.1:8104/overlay/`

### Option 3: Add Real TTS

Terminal 1:

```powershell
uv run python -m services.tts_bridge.main --serve
```

Terminal 2:

```powershell
uv run python -m uchat.cli
```

This only makes sense once you have prepared the vendor/runtime and model assets. Otherwise, letting the runtime fall back to console TTS is usually better.

### Option 4: Add Bilibili Live Input

Terminal 1:

```powershell
uv run python -m services.bilibili_gateway.main serve
```

Terminal 2:

```powershell
uv run python -m uchat.cli
```

### Option 5: Add Body Performance and Lipsync Sidecars

Body service:

```powershell
uv run python -m services.body_service.main --serve
```

Lipsync sidecar:

```powershell
uv run python -m services.lipsync_bridge.main --serve
```

Neither is a hard dependency of the main runtime. They are best added after text, subtitles, and TTS are already stable.

### Option 6: Start Identity Governance

```powershell
uv run python -m services.identity_admin.main serve
```

## Recommended Integration Order

1. Start with `uchat.cli` and confirm the text pipeline and model config work.
2. Add `obs_bridge` and confirm subtitle delivery.
3. Add `tts_bridge` and confirm sentence playback and subtitle sync.
4. Add `bilibili_gateway` and confirm structured live events reach the runtime.
5. Add `body_service`, `lipsync_bridge`, and `identity_admin` last.

This makes problems much easier to isolate.

## Current Runtime Boundaries

- `uchat.cli` supports console input and live-input polling in parallel
- The runtime is now centered around `event normalization -> memory/context -> LLM -> sentence-level output -> TTS/OBS dispatch`
- `tts_bridge` is an external service, not runtime-internal playback logic
- `body_service` is now a real sidecar, not just a placeholder directory
- `lipsync_bridge` only mirrors audio on a best-effort basis and does not block TTS
- `identity_admin` handles human identity governance, not reply decision-making

## Documentation Entry Points

Suggested first reads:

- [Chinese README](README.md)
- [docs/en/README.md](docs/en/README.md)
- [docs/en/project_structure_and_run.md](docs/en/project_structure_and_run.md)
- [docs/en/configuration.md](docs/en/configuration.md)
- [docs/en/runtime.md](docs/en/runtime.md)

If you are integrating a specific service, then continue with the matching module docs and `services/*/README.md`.

## Public Repo Constraints

If you keep developing in this public directory, it is a good idea to keep these constraints:

- Do not commit `.env`
- Do not commit databases or generated local artifacts
- Do not commit private models, audio, or vendor/runtime directories
- Do not commit private cookies, tokens, or sessions
- Do not treat the public example identity as a production-ready character prompt
