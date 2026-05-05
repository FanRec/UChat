# bilibili_gateway

`bilibili_gateway` 是 UChat 直播热路径前处理层。它把 B 站直播原始事件整理成低延迟、低噪声、可结构化消费的输入，避免把平台复杂性、礼物连击复读和高弹幕压力重新塞回 core runtime。

## 当前职责

- 连接真实或 mock 直播房间
- 标准化弹幕、礼物、SC、关注、房间状态
- 去重与短时爆发折叠
- 普通弹幕聚合
- 礼物连击跟踪与节流
- 直播场景统计
- 轻量风险标签与回复策略占位
- 通过 HTTP 拉取接口向 `uchat-core` 提供结构化事件

## 明确不做

- 不承担最终回复决策
- 不承担身份真源绑定
- 不承担完整 moderation 深裁决
- 不做 B 站出站发言主线
- 不长成第二个 runtime

## 服务接口

- `GET /health`
- `GET /ready`
- `POST /v1/bilibili/connect`
- `POST /v1/bilibili/disconnect`
- `GET /v1/bilibili/events`
- `POST /v1/bilibili/test-events`

## 配置

服务唯一静态配置源在 [config/service.toml](./config/service.toml)。

其中包含：

- 服务监听地址
- 直播房间 ID
- 认证 cookie/session 配置
- 去重 / 聚合 / 场景统计窗口
- 礼物连击 quiet timeout / milestone / 节流
- 事件队列保留与拉取限制
- 风险规则文件与默认回复策略
- `offline_history / offline_test` 联调开关
- live 诊断 debug JSON 输出目录

直播登录态建议写在仓库根目录 `.env`：

```dotenv
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_BUVID3=
BILIBILI_DEDEUSERID=
```

`bilibili_gateway` 启动时会自动读取根目录 `.env`，再展开 `service.toml` 里的 `${...}` 变量。

## 运行

开发环境推荐直接使用仓库级 `uv` 环境：

```bash
uv run uvicorn services.bilibili_gateway.main:app --host 127.0.0.1 --port 8110
```

Windows 下也可以直接双击运行：

```text
services\bilibili_gateway\start_gateway.bat
```

如果手工从终端启动，优先使用模块方式，避免 Windows 下脚本路径启动时的包导入问题：

```bash
uv run python -m services.bilibili_gateway.main serve
```

如果 `mock_mode = true`，服务不会主动连接真实房间，但 HTTP 主链与结构化事件流程仍可测试。

如果需要在未开播时联调主链，可以启用 `[gateway.testing].offline_test_mode_enabled = true`。但默认连接语义现在固定为：

- 未显式指定 `connection_mode` 且 `mock_mode = false` 时，默认是 `live`
- `connection_mode = live` 时，只跑真实 websocket 链路
- `connection_mode = offline_history` 时，显式跑历史弹幕轮询
- `connection_mode = offline_test` 时，只建立网关房间状态并允许 `/test-events` 注入

也就是说，`offline_test_mode_enabled = true` 只表示“允许联调模式存在”，不再表示“live 失败时自动偷偷改走 offline_history”。

如果你确实希望 live 建连失败后显式退回 `offline_history`，需要在 `service.toml` 里同时配置：

```toml
[gateway.testing]
offline_test_mode_enabled = true
live_connect_fallback_mode = "offline_history"
```

显式 `offline_history` 模式会轮询：

```text
https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory?roomid=...
```

下游仍然复用同一条标准化、去重、聚合和 `/events` 输出主链。

开播后的真实直播链路则固定走 `live websocket`。CLI 在 `scene_kind = "live_stream"` 下也会显式请求 `connection_mode = "live"`，避免被测试开关默认绕走。

真实 live 联调时建议显式写出：

```json
POST /v1/bilibili/connect
{
  "room_id": "24194689",
  "connection_mode": "live"
}
```

如果需要离线联调，再切到显式测试模式：

```json
POST /v1/bilibili/connect
{
  "room_id": "24194689",
  "connection_mode": "offline_test"
}
```

再向：

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

这两种测试方式都只停留在网关侧，不会把平台测试特例扩散进 core runtime。

`UChat` 侧当前也已补 live 输入恢复能力：

- 可以先启动 `UChat`，再启动 `bilibili_gateway`
- `bilibili_gateway` 中途挂掉后，只需要把 gateway 自身重启
- live adapter 会保留控制台主循环，并按 backoff 自动重试接回 gateway
- 如果 gateway 重启导致旧事件游标失效，adapter 会自动重置 cursor 并恢复拉取

也就是说，`bilibili_gateway` 现在可以视为“可后开、可单独重启恢复”的外部输入服务，而不需要每次都连带重启整套 `UChat` runtime。

## 真实 live 诊断

开启 `debug_dump_enabled = true` 后，网关会在 `debug_dump_dir/<room_id>/live_diagnostics.json` 写入 live 诊断信息，包含：

- `requested_connection_mode / effective_connection_mode`
- `live_status`
- `login_uid`
- `host_server_count / host_server_token_ready`
- `heartbeat_count / last_heartbeat_at_ms / last_popularity`
- `business_message_count / last_business_event_type / last_business_message_at_ms`
- `last_stop_reason / last_stop_exception_class / last_stop_classification`
- 最近一段 `stages`

后台彩色日志会同步按阶段输出 `connect_mode / room_init_check / client_init_room / websocket_start / live_heartbeat / live_event / live_stop`。

## 礼物连击防复读

同一用户连续送出同一礼物时，网关不会逐笔把事件推给 core：

- 首次礼物：`bilibili_gift`
- 中途 milestone：`bilibili_gift_combo_update`
- quiet timeout 结束：`bilibili_gift_combo_summary`

默认 reply candidate 上限为同一 combo 两次：

1. `combo_started`
2. `combo_summary`

## 服务内文档

- 开发进度见 [progress.md](./progress.md)
- 真实事件流说明见 [technical_real_event_flow.md](./technical_real_event_flow.md)
