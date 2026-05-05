# 运行时主链

## 主入口

入口文件：

- [uchat/cli.py](../uchat/cli.py)

它负责：

1. 加载 `.env`、`config/app.toml` 和 `config/models.toml`
2. 初始化 logging、LTMem、memory、model router、prompt manager
3. 构建输出观察器、TTS 适配器、OBS 字幕适配器和身份服务
4. 创建 `RuntimeOrchestrator`
5. 根据配置进入控制台循环或“控制台 + 直播输入轮询”模式

## runtime 分层

### `RuntimeOrchestrator`

负责：

- scene 注册
- 前后台场景调度
- 事件入口统一化
- 把事件交给具体 `SessionRuntime`

### `SessionRuntime`

这是单个 scene 的 turn 编排核心，当前主链会经过：

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

### 输出层

runtime 会把 LLM 的句级结果转成输出任务，再分发给：

- 文本观察器
- TTS
- OBS 字幕

其中：

- 没有可用的 TTS 服务时，会退回控制台 TTS
- 没有可用的 OBS 服务时，字幕输出可以为空

## 当前输入模式

### 控制台模式

只有 `uchat.cli` 自身即可工作，适合：

- prompt 调试
- 模型配置调试
- 单机文本主链验证

### 直播模式

当满足以下条件时，CLI 会额外接入 `bilibili_gateway`：

- `runtime.scene_kind = "live_stream"`
- `[services.platform.bilibili].url` 已配置

即使直播输入连接失败，CLI 也会继续保留控制台输入，并在后台自动重试。

## 典型流程

控制台输入：

```text
用户输入
-> NormalizedEvent
-> SessionRuntime
-> prompt + LLM
-> 句级文本分片
-> output dispatch
-> text / TTS / OBS
```

直播输入：

```text
bilibili_gateway 结构化事件
-> RuntimeOrchestrator
-> SessionRuntime
-> timing gate
-> prompt + LLM
-> 句级输出
-> TTS / OBS / console view
```

## 调试入口

- `debug/traces/<trace_id>/`
- `message_chain.json`
- 控制台 `conversation` / `timeline` 视图
- 结构化日志目录 `logs/`

如果你在公开版里遇到问题，优先先确认：

1. 配置是否加载成功。
2. 模型路由是否能正常出回复。
3. 外部服务 URL 是否真的可访问。
