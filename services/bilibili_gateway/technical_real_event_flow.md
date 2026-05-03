# bilibili_gateway 真实事件流

## 目标

`bilibili_gateway` 负责把直播平台原始事件压缩成低噪声、可结构化消费的热路径输入，不承担最终回复决策。

## 主链

```text
blivedm raw event
  -> normalize
  -> target guess
  -> lightweight value score
  -> dedupe / burst fold
  -> gift combo tracking / danmaku aggregation
  -> scene stats update
  -> risk tag
  -> event store
  -> /v1/bilibili/events poll
```

未开播联调时，固定走：

```text
/v1/bilibili/test-events
  -> normalize
  -> target guess
  -> lightweight value score
  -> dedupe / aggregation / scene stats
  -> event store
  -> /v1/bilibili/events poll
```

若显式指定 `connection_mode = offline_history`，会走：

```text
offline history poll
  -> /xlive/web-room/v1/dM/gethistory
  -> normalize
  -> target guess
  -> lightweight value score
  -> dedupe / aggregation / scene stats
  -> event store
  -> /v1/bilibili/events poll
```

## 连接模式边界

- `live`
  - 只做真实 websocket 链路。
  - 连接失败默认直接失败，不偷偷切成别的模式。
  - 只有当 `[gateway.testing].live_connect_fallback_mode = "offline_history"` 时，才允许显式 fallback。
- `offline_history`
  - 只做历史弹幕轮询联调。
  - 适合未开播、cookie/房态不稳定时继续验证 gateway -> adapter -> runtime 主链。
- `offline_test`
  - 不建真实 websocket。
  - 只保留房间上下文并允许 `/v1/bilibili/test-events` 注入。

## live 诊断主线

真实 live 连接当前固定输出这些可观测阶段：

```text
connect_mode
  -> room_init_check
  -> client_create
  -> client_init_room
  -> websocket_start
  -> live_heartbeat
  -> live_event
  -> live_stop
```

诊断信息既会进入后台彩色日志，也会落到：

```text
debug/bilibili_gateway/<room_id>/live_diagnostics.json
```

重点观察字段：

- `live_status`
- `login_uid`
- `host_server_count`
- `host_server_token_ready`
- `heartbeat_count`
- `business_message_count`
- `last_business_event_type`
- `last_stop_reason`
- `last_stop_exception_class`
- `last_stop_classification`

## 礼物连击策略

- 首次礼物：`bilibili_gift`
- 连击 milestone：`bilibili_gift_combo_update`
- quiet timeout 结束：`bilibili_gift_combo_summary`

默认不让同一 combo 逐笔进入 reply candidate。

## 输出事件固定类型

- `bilibili_danmaku`
- `bilibili_room_state`
- `bilibili_gift`
- `bilibili_gift_combo_update`
- `bilibili_gift_combo_summary`
- `bilibili_sc`
- `bilibili_follow`
