# tts_bridge

`tts_bridge` 负责把 runtime 的 `tts` 任务转换成真实音频播放生命周期，并把必要的状态反馈给上层。

## 它负责什么

- 接收来自 `uchat` 的句子级 TTS 任务
- 调用底层 vendor 生成音频
- 处理播放顺序、取消和 turn-end
- 向 OBS 字幕链路推送播放侧同步事件

## 它不负责什么

- 不负责内容审核
- 不负责平台协议
- 不负责最终回复生成
- 不负责身份绑定

## 启动方式

```bash
uv run python -m services.tts_bridge.main --serve
```

或者先只检查入口：

```bash
uv run python -m services.tts_bridge.main
```

## 公开版默认不附带什么

- TTS 模型权重
- 参考音频
- vendor/runtime 目录

这些需要你自己准备，并在 `services/tts_bridge/config/service.toml` 中补好路径。

## 配置说明

主要配置文件：

- `services/tts_bridge/config/service.toml`

关键段落：

- `[vendor]`
  - vendor 入口、python 可执行文件、模型配置路径
- `[preset]`
  - 参考音频和 prompt
- `[streaming]`
  - 流式播放参数
- `[subtitle_sync]`
  - OBS 字幕同步设置

## 与 OBS 的协同

当 `[subtitle_sync].enabled = true` 时：

- 播放侧会向 `obs_bridge` 推送字幕状态
- 能拿到播放进度时逐字同步
- 不能拿到细粒度进度时，会做估算式逐字推进

## 常见问题

### 为什么启动后没有声音？

通常是因为：

- 没有准备 vendor/runtime
- 模型文件路径不正确
- `ref_audio_path` 不存在

### 为什么字幕没有跟着走？

先检查：

1. `tts_bridge /health` 里的 `subtitle_sync_enabled`
2. `obs_bridge` 是否已启动
3. `subtitle_sync.obs_base_url` 是否正确

## 相关文档

- [公开版 README](../../README.md)
- [OBS 字幕模块](../obs_bridge/README.md)
