# bilibili_gateway 开发进度

更新时间：2026-05-01

## 已完成

- 服务级静态配置已统一收口到 `config/service.toml`
- 首版 FastAPI HTTP 服务骨架已落地
- 已固定 `/health /ready /v1/bilibili/connect /disconnect /events`
- 已把 `live / offline_history / offline_test` 三种连接模式语义明确收口，不再让 `offline_test_mode_enabled` 偷偷改写 live 默认行为
- 已保留 `/v1/bilibili/test-events` 兜底联调入口，避免平台侧能力波动阻塞 core 主链开发
- 已升级 `blivedm` 到 `xfgryujk/blivedm` GitHub 新版并适配新版 handler API，修复旧版 `getDanmuInfo -> -352` 的已知阻塞
- 已完成 gateway 内部第一轮拆分：`connectors / processors / stores / event_builder / service`
- 已落地房间级 `event_store / gift_combo_tracker / danmaku_aggregator / scene_stats`
- 已落地礼物连击“起始一次 + milestone 节流 + 最终汇总”主链
- 已为 core 提供扩展结构化事件字段占位
- 已补 live 连接诊断：`room_init_check / client_init_room / websocket_start / live_heartbeat / live_event / live_stop` 阶段日志与 `live_diagnostics.json`
- 已补 live 失败时不崩主进程、CLI 保留 console 模式、`uid=None` 登录态路径与 callback 同步 ingest 的回归测试

## 进行中

- 真实 `blivedm` 房间长时间稳定性观察
- 礼物 / SC / 关注 / 房间状态事件语义校准
- 与 core 的扩展 `NormalizedEvent` 消费联调

## 下一步

1. 以真实房间 smoke 连续验证 `connect -> heartbeat -> danmaku -> adapter -> runtime message_chain`
2. 继续观察 websocket 重连时的 stop reason、heartbeat-only、业务消息缺失等模式
3. 补更完整的风险规则与互聊识别
4. 增补高噪声场景 replay 样本
