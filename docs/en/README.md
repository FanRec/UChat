# Documentation Index

<p>
  <a href="../../README.md">中文首页</a> | <a href="../../README.en.md">English Home</a> | <a href="../README.md">中文文档</a>
</p>

This `docs/en/` directory is the English documentation entry point for `UChat_Public`. Its goal is to help you understand the current code structure, startup paths, and service boundaries quickly.

## Recommended Reading Order

1. Root [README.en.md](../../README.en.md)
2. [Project Structure and Startup Paths](project_structure_and_run.md)
3. [Configuration Guide](configuration.md)
4. [Runtime Pipeline](runtime.md)
5. [Character and Identity Notes](runtime_identity.md)
6. [TTS Module](tts_bridge.md)
7. [OBS Subtitle Module](obs_bridge.md)
8. [Bilibili Gateway](bilibili_gateway.md)
9. [Identity Admin Module](identity_admin.md)
10. [Body Service Module](body_service.md)

## Reading Advice

- If this is your first time with the repo, start with the first four pages.
- If you mainly want to get it running, focus on startup paths and configuration first.
- If you are integrating one specific sidecar, then move into the matching `docs/en/*.md` file and `services/*/README.md`.

## Documentation Scope

- These docs prioritize the real boundaries that currently exist in the public repository.
- Some service directories still contain deeper progress logs and technical notes. Those are better read when you are working on that specific service.
- The public docs intentionally avoid private environment values, private asset paths, or internal deployment conventions.
