# 项目结构与运行方式

## 目录结构

### `uchat/`

核心运行时代码：

- `cli.py`
  - 本地入口
- `runtime/`
  - turn 编排、输出调度、trace
- `adapters/`
  - 输入输出适配器
- `identity/`
  - 身份解析与本地存储
- `models/`
  - LLM 路由与 OpenAI 兼容客户端
- `config/`
  - 主配置加载

### `services/`

独立服务边界：

- `tts_bridge/`
- `obs_bridge/`
- `bilibili_gateway/`
- `identity_admin/`
- `body_service/`

### `config/`

- `app.toml`
  - 主运行配置
- `models.toml`
  - 模型供应商、档案与路由

### `prompts/`

- `prompts/<locale>/*.prompt`

### `docs/`

公开版说明文档

## 最小运行组合

### 只跑 LLM 主链

```bash
uv run python -m uchat.cli
```

### 跑 LLM + OBS 字幕

终端 1：

```bash
uv run python -m services.obs_bridge.main --serve
```

终端 2：

```bash
uv run python -m uchat.cli
```

### 跑 LLM + OBS + TTS

终端 1：

```bash
uv run python -m services.obs_bridge.main --serve
```

终端 2：

```bash
uv run python -m services.tts_bridge.main --serve
```

终端 3：

```bash
uv run python -m uchat.cli
```

### 跑直播输入链路

终端 1：

```bash
uv run python -m services.bilibili_gateway.main serve
```

终端 2：

```bash
uv run python -m uchat.cli
```

并把 `config/app.toml` 里的 `scene_kind` 调整到合适场景。

## 推荐排障顺序

1. 先确认 `uchat.cli` 能正常回 LLM 文本
2. 再确认 `obs_bridge` 可连
3. 再确认 `tts_bridge` 是否 ready
4. 最后再接入 `bilibili_gateway`
