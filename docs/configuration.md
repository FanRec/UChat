# 配置说明

## 1. `config/app.toml`

主运行配置，核心内容包括：

- `runtime`
  - 场景 ID、会话 ID、locale、角色 identity
- `scene_defaults`
  - 场景默认值
- `timing_gate`
  - 回复节奏和阈值
- `identity_store`
  - `memory` 或 `sqlite`
- `prompt`
  - prompt 根目录与版本
- `ltmem`
  - LTMem 服务设置
- `debug`
  - 调试工件和控制台视图
- `logging`
  - 日志设置
- `services.*`
  - 外部服务地址与超时

### 公开版特别说明

- `runtime.identity` 现在只是公开示例，请自行替换。
- 不要把真实 token、cookie、API key 写进 `app.toml`。
- 数据库默认是：
  - `data/identity.sqlite3`
  - 启动时会自动创建

## 2. `config/models.toml`

用于定义：

- provider
- profile
- route

当前示例默认使用 `deepseek` 的 OpenAI 兼容接口。

你至少需要准备：

```dotenv
DEEPSEEK_API_KEY=...
```

## 3. `.env`

公开仓库里只保留 `.env.example`。

本地应自行创建 `.env`，常见内容：

```dotenv
DEEPSEEK_API_KEY=
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_BUVID3=
BILIBILI_DEDEUSERID=
```

## 4. 服务内配置

各服务还有自己的 `config/service.toml`：

- `services/tts_bridge/config/service.toml`
- `services/obs_bridge/config/service.toml`
- `services/bilibili_gateway/config/service.toml`
- `services/identity_admin/config/service.toml`
- `services/body_service/config/service.toml`

需要联调哪个服务，就重点检查对应文件。
