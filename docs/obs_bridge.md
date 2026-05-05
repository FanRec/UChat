# OBS 字幕模块

对应目录：

- [services/obs_bridge](../services/obs_bridge)

## 它负责什么

- 接收 runtime 或 `tts_bridge` 的字幕事件
- 维护当前字幕状态快照
- 通过 WebSocket 推给 OBS Browser Source
- 渲染分句和逐字推进字幕

## 如何启动

```powershell
uv run python -m services.obs_bridge.main --serve
```

## OBS 配置

在 OBS 添加 Browser Source：

- URL: `http://127.0.0.1:8104/overlay/`

你也可以先在浏览器直接访问这个地址，确认 overlay 页面能正常打开。

## 当前语义

- 一个分句一行
- 新分句通常追加在最下方
- 新一轮回复开始前会清掉上一轮
- `trace_id + generation_id` 用来管理字幕生命周期

## 常见排障点

先看：

- `http://127.0.0.1:8104/health`
- `http://127.0.0.1:8104/overlay/`

如果 overlay 页面正常，但没有字幕：

1. 看 runtime 是否真的在发句级字幕事件。
2. 看 `tts_bridge` 是否启用了字幕同步。
3. 看 `obs_bridge` 控制台日志或 `/health` 返回值。
