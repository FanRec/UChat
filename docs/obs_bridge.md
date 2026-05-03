# OBS 字幕模块

对应目录：

- [services/obs_bridge](../services/obs_bridge)

## 它负责什么

- 接收 runtime 或 `tts_bridge` 的字幕事件
- 维护字幕状态快照
- 通过 WebSocket 推给 OBS Browser Source
- 渲染逐字 / 分句字幕

## 如何启动

```bash
uv run python -m services.obs_bridge.main --serve
```

## OBS 配置

在 OBS 添加 Browser Source：

- URL: `http://127.0.0.1:8104/overlay/`

## 当前语义

- 一个分句一行
- 新分句在最下方
- 新一轮回复开始前清掉上一轮
- `trace_id + generation_id` 管理生命周期

## 排障

先看：

- `http://127.0.0.1:8104/health`
- 浏览器直接访问 `http://127.0.0.1:8104/overlay/`

如果页面有但没字幕：

1. 看 runtime 是否在发句级字幕
2. 看 `tts_bridge /health.subtitle_sync_enabled`
3. 看 `obs_bridge` 控制台日志
