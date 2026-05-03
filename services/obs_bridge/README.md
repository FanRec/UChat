# obs_bridge

`obs_bridge` 负责把实时字幕状态推送给 OBS Browser Source。

## 它负责什么

- 接收 runtime 或 `tts_bridge` 推来的字幕事件
- 维护字幕会话状态
- 通过 WebSocket 推送给 OBS 浏览器源
- 提供一个可直接挂到 OBS 的网页 overlay

## 它不负责什么

- 不负责 LLM 回复生成
- 不负责 TTS 合成
- 不负责身份绑定
- 不负责平台协议

## 接口
 
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/v1/obs/subtitle` | 接收字幕事件 |
| POST | `/v1/obs/status` | 接收状态事件 |
| POST | `/v1/obs/cancel` | 取消字幕任务 |
| WS | `/ws/subtitle` | WebSocket 字幕快照推送 |
| GET | `/overlay/` | Browser Source 渲染页面 |

## 使用方式

1. 启动服务
2. 在 OBS 里加 Browser Source
3. URL 指向 `http://127.0.0.1:8104/overlay/`

## 显示语义

- 一个分句占一行
- 新分句出现在最下方
- 新一轮回复开始前会清掉上一轮
- 句子播放时逐字推进
- 已读句和当前句尽量保持统一视觉风格

## 常见排障

先看：

- `/health`
- `/overlay/`
- 控制台日志

如果没有字幕，通常是：

- `tts_bridge` 没开 `subtitle_sync`
- `tts_bridge` 没连上
- `obs_base_url` 配错

## 相关文档

- [公开版 README](../../README.md)
- [TTS 模块](../tts_bridge/README.md)

## 启动

```bash
python -m services.obs_bridge.main --serve
```

## OBS 配置

1. 启动 obs_bridge 服务
2. OBS → 来源 → 添加 → Browser Source
3. URL 设为 `http://127.0.0.1:8104/overlay`
4. 勾选"自定义帧率"，设为 30fps
5. 勾选"关源时关闭浏览器"

建议排障顺序：

1. 打开 `http://127.0.0.1:8104/health`，确认 `ws_connections` 与 overlay 配置正常。
2. 浏览器直接访问 `http://127.0.0.1:8104/overlay/`，确认页面可连上 `/ws/subtitle`。
3. 观察 `obs_bridge` 控制台日志，确认是否收到 `sentence / segment_progress / clear / turn_end`。
4. 若 runtime 使用 `ServiceTTSAdapter`，再确认 `tts_bridge /health` 的 `subtitle_sync_enabled` 是否为 `true`。

## 配置

配置文件：`config/service.toml`

关键配置项（`[overlay]` 段）：

- `typewriter_speed_ms` — 打字机效果速度（毫秒/字符）
- `turn_end_fade_delay` — 整轮结束后的淡出延迟（秒）
- `max_lines` — 最大同时显示行数
- `active_color` — 当前句高亮色
- `dim_color` — 已说完的句子变暗色
- `position` — 字幕位置（bottom_center / bottom_left / bottom_right / top_center）
- `font_size` / `background` / `border_radius` — 样式控制

## 目录结构

```
services/obs_bridge/
  __init__.py          # 包导出
  main.py              # 入口点
  app.py               # FastAPI 应用工厂 + 路由
  config.py            # TOML 配置加载
  service.py           # HTTP handler + WS 广播装配
  subtitle_state.py    # generation-safe 字幕状态存储
  config/
    service.toml       # 服务配置
  overlay/
    index.html         # Browser Source 页面
    style.css          # 字幕样式
    main.js            # WebSocket 客户端 + 动画逻辑
```
