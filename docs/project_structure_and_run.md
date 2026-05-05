# 项目结构与运行方式

## 目录结构

### `uchat/`

核心运行时代码：

- `cli.py`
  - 本地主入口，负责启动配置、runtime、输入输出适配器和可选直播轮询
- `runtime/`
  - turn 编排、场景调度、输出生命周期、trace
- `adapters/`
  - 控制台、Bilibili、TTS、OBS 等适配器
- `identity/`
  - 身份服务和本地身份存储
- `models/`
  - 模型 provider、profile、route 和 OpenAI 兼容客户端
- `config/`
  - 主配置加载和结构化设置对象

### `services/`

独立服务边界：

- `bilibili_gateway/`
  - 直播事件输入网关
- `tts_bridge/`
  - TTS 合成、播放和字幕同步
- `obs_bridge/`
  - OBS 字幕 overlay
- `body_service/`
  - 身体表现执行 sidecar
- `lipsync_bridge/`
  - 口型旁路镜像 sidecar
- `identity_admin/`
  - 身份治理 HTTP 服务

### `config/`

- `app.toml`
  - 主运行配置、LTMem、debug、logging、服务端点
- `models.toml`
  - 模型供应商、模型档案和逻辑路由

### `prompts/`

- `prompts/<locale>/*.prompt`

### `docs/`

公开版说明文档。

## 最小运行组合

### 只跑 LLM 主链

```powershell
uv run python -m uchat.cli
```

适合先确认：

- `.env` 是否就绪
- `config/models.toml` 是否可用
- prompt 和 runtime 主链是否能正常出文本

### 跑 LLM + OBS 字幕

终端 1：

```powershell
uv run python -m services.obs_bridge.main --serve
```

终端 2：

```powershell
uv run python -m uchat.cli
```

### 跑 LLM + OBS + TTS

终端 1：

```powershell
uv run python -m services.obs_bridge.main --serve
```

终端 2：

```powershell
uv run python -m services.tts_bridge.main --serve
```

终端 3：

```powershell
uv run python -m uchat.cli
```

### 跑直播输入链路

终端 1：

```powershell
uv run python -m services.bilibili_gateway.main serve
```

终端 2：

```powershell
uv run python -m uchat.cli
```

说明：

- `uchat.cli` 是否接入直播输入，取决于 `runtime.scene_kind` 和 `[services.platform.bilibili].url`
- 即使直播输入接不上，CLI 仍会继续保留控制台模式运行

### 跑身体表现与口型旁路

```powershell
uv run python -m services.body_service.main --serve
```

```powershell
uv run python -m services.lipsync_bridge.main --serve
```

这两个服务都属于 sidecar，建议在文本、字幕和 TTS 稳定后再接。

### 跑身份治理服务

```powershell
uv run python -m services.identity_admin.main serve
```

## 推荐排障顺序

1. 先确认 `uchat.cli` 能正常回 LLM 文本。
2. 再确认 `obs_bridge` 的 `/health` 和 overlay 页面可访问。
3. 再确认 `tts_bridge` 是否 ready，且资源路径有效。
4. 再接入 `bilibili_gateway` 观察结构化事件。
5. 最后再联调 `body_service`、`lipsync_bridge` 和 `identity_admin`。

## 公开版理解方式

这个仓库更适合被理解为：

- 一个可跑的 runtime 主链
- 若干独立 sidecar 的接入示例
- 一套方便你替换模型、角色、平台和执行层的工程骨架

它不是把所有资源和私有运维条件都打包好的“一键成品”。
