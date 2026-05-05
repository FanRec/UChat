# 配置说明

## 1. `config/app.toml`

主运行配置，当前主要包含：

- `runtime`
  - `scene_id`、`session_window_id`、`locale`
  - `identity`
  - `audience_scope`
  - `scene_kind`
  - 短期历史窗口和保留上限
- `scene_defaults`
  - 默认节目/话题/风险档位等场景状态
- `timing_gate`
  - 回复阈值、拥堵阈值、低价值过滤规则
- `identity_store`
  - `memory` 或 `sqlite`
- `prompt`
  - prompt 根目录和版本
- `ltmem`
  - LTMem HTTP 服务模式和健康检查行为
- `debug`
  - trace 工件、控制台视图、预览开关
- `logging`
  - 结构化日志设置
- `services.*`
  - 外部服务地址、是否 required、超时配置

### 公开版特别说明

- `runtime.identity` 只是公开示例，请自行替换。
- 不要把真实 token、cookie、API key 写进 `app.toml`。
- 默认 SQLite 身份库路径是 `data/identity.sqlite3`。
- `scene_kind = "live_stream"` 不等于一定连接直播；真正是否接入 B 站输入，还要看 `[services.platform.bilibili].url` 是否可用。

## 2. `config/models.toml`

用于定义三层模型配置：

- `providers.<provider_id>`
- `profiles.<profile_id>`
- `routes.<role_name>`

当前公开示例默认使用 `deepseek` 的 OpenAI 兼容接口，并提供了：

- `replyer`
- `planner`
- `timing_gate`
- `safety`
- `summarizer`

至少需要准备：

```dotenv
DEEPSEEK_API_KEY=...
```

## 3. `.env`

公开仓库里只保留 `.env.example`，本地请自行创建 `.env`。

常见内容：

```dotenv
DEEPSEEK_API_KEY=
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_BUVID3=
BILIBILI_DEDEUSERID=
```

说明：

- 不是所有变量都必填。
- 只跑控制台文本主链时，通常只需要模型 API Key。
- 真实 B 站联调才需要登录态相关变量。

## 4. 服务内配置

各服务还有自己的 `config/service.toml`：

- `services/tts_bridge/config/service.toml`
- `services/obs_bridge/config/service.toml`
- `services/bilibili_gateway/config/service.toml`
- `services/identity_admin/config/service.toml`
- `services/body_service/config/service.toml`
- `services/lipsync_bridge/config/service.toml`

如果你在联调某个服务，优先检查它自己的配置文件，不要把服务私有参数混进主 `app.toml`。

## 5. 配置排查建议

建议按下面顺序排查：

1. 先看 `.env` 里模型密钥是否存在。
2. 再看 `config/models.toml` 的 provider 和 route 是否能对上。
3. 再看 `config/app.toml` 中对应服务 URL 是否正确。
4. 最后检查具体 `services/*/config/service.toml` 中的资源路径、端口和开关。
