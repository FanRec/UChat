# Runtime Pipeline

## Main Entry

Entry file:

- [uchat/cli.py](../../uchat/cli.py)

It is responsible for:

1. Loading `.env`, `config/app.toml`, and `config/models.toml`
2. Initializing logging, LTMem, memory, the model router, and the prompt manager
3. Building the console observer, TTS adapter, OBS subtitle adapter, and identity service
4. Creating `RuntimeOrchestrator`
5. Entering console mode or the combined console + live-polling mode

## Runtime Layers

### `RuntimeOrchestrator`

Responsible for:

- scene registration
- foreground/background scene coordination
- unified event entry
- dispatching events into the correct `SessionRuntime`

### `SessionRuntime`

This is the core turn orchestrator for a single scene. The current main path goes through:

- `normalize`
- `scene_state_update`
- `identity_resolve`
- `moderation`
- `memory_ingest`
- `context_build`
- `timing_gate`
- `prompt_render`
- `llm_streaming`
- `output_dispatch`

### Output Layer

The runtime turns sentence-level LLM results into output tasks and dispatches them to:

- text observers
- TTS
- OBS subtitles

Notes:

- If no TTS service is available, the runtime falls back to console TTS
- If no OBS service is available, subtitle output can simply be absent

## Current Input Modes

### Console Mode

`uchat.cli` can run by itself. This is useful for:

- prompt debugging
- model config debugging
- local text-pipeline verification

### Live Mode

The CLI also connects to `bilibili_gateway` when:

- `runtime.scene_kind = "live_stream"`
- `[services.platform.bilibili].url` is configured

Even if live input fails, the CLI keeps console input enabled and retries in the background.

## Typical Flows

Console input:

```text
user input
-> NormalizedEvent
-> SessionRuntime
-> prompt + LLM
-> sentence-level text chunks
-> output dispatch
-> text / TTS / OBS
```

Live input:

```text
bilibili_gateway structured event
-> RuntimeOrchestrator
-> SessionRuntime
-> timing gate
-> prompt + LLM
-> sentence-level output
-> TTS / OBS / console view
```

## Debug Entry Points

- `debug/traces/<trace_id>/`
- `message_chain.json`
- console `conversation` / `timeline` views
- structured logs in `logs/`

If something is wrong in the public repo, the quickest checks are:

1. Did configuration load successfully
2. Can the model router produce replies
3. Are the external service URLs actually reachable
