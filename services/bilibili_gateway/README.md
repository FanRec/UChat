# bilibili_gateway

`bilibili_gateway` 是 UChat 的直播输入网关。

它负责把 B 站直播原始事件整理成结构化输入，交给 runtime 消费。

## 它负责什么

- 连接真实或测试房间
- 标准化弹幕、礼物、SC、关注、房间状态
- 去重、聚合和轻量场景统计
- 提供 HTTP 拉取接口给 core runtime

## 它不负责什么

- 不做最终回复决策
- 不做完整身份真源
- 不做最终 moderation 裁决
- 不做直播出站发言

## 接口

- `GET /health`
- `GET /ready`
- `POST /v1/bilibili/connect`
- `POST /v1/bilibili/disconnect`
- `GET /v1/bilibili/events`
- `POST /v1/bilibili/test-events`

## 配置

配置文件：

- `services/bilibili_gateway/config/service.toml`

常见配置包括：

- 房间 ID
- cookie 登录态
- 聚合与限频窗口
- debug dump 目录
- offline/live 联调模式

## 环境变量

如果你要联调真实 B 站直播输入，建议在根目录 `.env` 里提供：

```dotenv
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_BUVID3=
BILIBILI_DEDEUSERID=
```

## 启动

```bash
uv run python -m services.bilibili_gateway.main serve
```

Windows 也可以直接使用：

```text
services\bilibili_gateway\start_gateway.bat
```

## 联调方式

### 1. mock / offline 测试

适合先验证：

- 网关配置
- 事件标准化
- runtime 对接

### 2. live 模式

适合真实房间联调。

CLI 在 `scene_kind = "live_stream"` 场景下，会优先请求：

- `connection_mode = "live"`

### 3. `/test-events`

你也可以直接手动注入测试事件，例如：

```json
POST /v1/bilibili/test-events
{
  "room_id": "24194689",
  "event_type": "danmaku",
  "raw": {
    "uid": "u1001",
    "uname": "测试观众",
    "msg": "主播今天唱歌吗？"
  }
}
```

## 调试

如果开启 debug dump，服务会写：

- `live_diagnostics.json`

可用于观察：

- 连接模式
- heartbeat
- websocket 业务消息
- 停止原因

## 相关文档

- [公开版 README](../../README.md)
- [docs/bilibili_gateway.md](../../docs/bilibili_gateway.md)
