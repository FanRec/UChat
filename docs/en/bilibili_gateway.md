# Bilibili Gateway

Directory:

- [services/bilibili_gateway](../../services/bilibili_gateway)

## Responsibilities

- connect to live rooms or offline event sources
- normalize live-stream events
- perform deduplication, light aggregation, and basic risk tagging
- provide structured input events to the core runtime

## Non-Responsibilities

- final reply decisions
- authoritative character identity
- final moderation decisions
- TTS, subtitles, or body execution

## Startup

```powershell
uv run python -m services.bilibili_gateway.main serve
```

## Real Login State

Provide these in local `.env`:

```dotenv
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_BUVID3=
BILIBILI_DEDEUSERID=
```

## Integration Advice

Suggested order:

1. make sure the gateway itself starts
2. make sure `[services.platform.bilibili].url` in `config/app.toml` points to it
3. start `uchat.cli` and confirm the runtime begins polling live events

If you do not want to connect to real live rooms immediately, try:

- `mock_mode`
- `offline_test`
- `/v1/bilibili/test-events`

That is usually the easiest way to validate the structured-event-to-runtime path first.
