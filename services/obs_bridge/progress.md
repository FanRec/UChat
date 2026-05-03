# obs_bridge 进度表

更新时间：2026-05-03

## 已完成

- 已把字幕状态从“最近消息列表”收口到显式 `SubtitleStateStore`。
- 已按 `trace_id + generation_id` 维护字幕会话，开始稳定处理 generation 切换。
- 已固定 WebSocket 广播协议为 `subtitle_state` 快照，并支持新连接 `replay`。
- 已支持 `sentence / segment_start / segment_progress / segment_complete / turn_end / clear`。
- 已让更大 generation 自动淘汰旧字幕，避免旧字幕残留。
- 已让 `clear` 清空当前及更旧 generation，适配打断与取消。
- 已收口为“同屏只保留当前一轮回复”，新一轮回复开始前会清掉上一轮字幕。
- 已把 overlay 视觉方向调整到更接近朴素、软一点、低遮挡的直播字幕样式。
- 已修复新句开始时偶发先整句闪出的前端回退问题。

## 进行中

- 继续和真实 OBS Browser Source 联调首连时序、逐字体感与 turn_end 收口观感。

## 风险

- 当前前端观感仍依赖真实 OBS 环境确认，单元测试只能覆盖协议与状态语义。
