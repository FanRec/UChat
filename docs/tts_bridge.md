# TTS 模块

对应目录：

- [services/tts_bridge](../services/tts_bridge)

## 它负责什么

- 接收 runtime 的句子级 TTS 请求
- 和底层 vendor/runtime 通信
- 做 ordered playback
- 做字幕播放侧同步
- 支持 cancel / cancel-trace / turn-end

## 关键文件

- `service.py`
  - 主业务逻辑
- `subtitle_sync.py`
  - 播放侧字幕同步
- `audio_playback.py`
  - 本地播放
- `sliding_window.py`
  - generation + segment 窗口状态
- `playback_coordinator.py`
  - 播放顺序协调
- `synthesis_scheduler.py`
  - streaming / batch 策略

## 如何启动

```bash
uv run python -m services.tts_bridge.main --serve
```

## 什么时候可能跑不起来

- 没有 vendor/runtime
- 没有模型权重
- `ref_audio_path` 不存在
- TTS 配置文件未准备好

## 当前公开版注意事项

- 仓库默认不附带模型和参考音频
- 你需要自己准备 `services/tts_bridge/config/service.toml` 所引用的资源
- 如果不准备真实 TTS，可以先不启这个服务

## 字幕同步

当 `subtitle_sync.enabled = true` 时：

- TTS 播放侧会向 `obs_bridge` 推送字幕事件
- 有播放进度时逐字同步
- 没有细粒度进度时，当前实现会退化为估算式逐字推进
