# Configuration Guide

## 1. `config/app.toml`

This is the main runtime configuration. It currently includes:

- `runtime`
  - `scene_id`, `session_window_id`, `locale`
  - `identity`
  - `audience_scope`
  - `scene_kind`
  - short-history render and retention limits
- `scene_defaults`
  - default show profile, topics, and risk-related scene state
- `timing_gate`
  - reply thresholds, congestion thresholds, and low-value filters
- `identity_store`
  - `memory` or `sqlite`
- `prompt`
  - prompt root directory and version
- `ltmem`
  - LTMem HTTP mode and health-check behavior
- `debug`
  - trace artifacts, console views, and preview switches
- `logging`
  - structured logging settings
- `services.*`
  - external service URLs, required flags, and timeout config

### Public Repo Notes

- `runtime.identity` is only a public example and should be replaced.
- Do not put real tokens, cookies, or API keys into `app.toml`.
- The default SQLite identity store path is `data/identity.sqlite3`.
- `scene_kind = "live_stream"` does not automatically mean live input is active. The actual Bilibili integration also depends on `[services.platform.bilibili].url`.

## 2. `config/models.toml`

This file defines the model configuration in three layers:

- `providers.<provider_id>`
- `profiles.<profile_id>`
- `routes.<role_name>`

The public example currently uses a DeepSeek OpenAI-compatible endpoint and includes routes for:

- `replyer`
- `planner`
- `timing_gate`
- `safety`
- `summarizer`

At minimum, you need:

```dotenv
DEEPSEEK_API_KEY=...
```

## 3. `.env`

The public repository only includes `.env.example`. Create your own local `.env`.

Typical content:

```dotenv
DEEPSEEK_API_KEY=
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_BUVID3=
BILIBILI_DEDEUSERID=
```

Notes:

- Not every variable is required.
- If you only want the console text pipeline, the model API key is usually enough.
- The Bilibili login variables are only needed for real live integration.

## 4. Service-Specific Config

Each service also has its own `config/service.toml`:

- `services/tts_bridge/config/service.toml`
- `services/obs_bridge/config/service.toml`
- `services/bilibili_gateway/config/service.toml`
- `services/identity_admin/config/service.toml`
- `services/body_service/config/service.toml`
- `services/lipsync_bridge/config/service.toml`

When you are integrating a specific service, prioritize its own config file instead of pushing private service settings into the top-level `app.toml`.

## 5. Suggested Config Debug Order

1. Check whether the model key exists in `.env`.
2. Check whether the provider and routes in `config/models.toml` line up.
3. Check whether the service URLs in `config/app.toml` are correct.
4. Check resource paths, ports, and switches in the relevant `services/*/config/service.toml`.
