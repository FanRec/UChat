# TTS 模块

对应目录：

- [services/tts_bridge](../services/tts_bridge)

## 它负责什么

- 接收 runtime 的句级 TTS 请求
- 与底层 vendor/runtime 通信
- 协调句级合成和有序播放
- 做播放侧字幕同步
- 支持 `cancel`、`cancel-trace`、`turn-end`
- 可 best-effort 通知 `body_service` 和 `lipsync_bridge`

## 关键文件

- `service.py`
  - 主业务逻辑和服务入口
- `synthesis_scheduler.py`
  - streaming / batch 策略
- `playback_coordinator.py`
  - 播放顺序协调
- `audio_playback.py`
  - 本地播放
- `subtitle_sync.py`
  - 播放侧字幕同步
- `sliding_window.py`
  - generation + segment 窗口状态
- `lipsync_bridge_client.py`
  - 对 lipsync sidecar 的旁路通知

## 如何启动

```powershell
uv run python -m services.tts_bridge.main --serve
```

如果需要做流式诊断，也可以查看该服务自身提供的诊断参数和 README。

## 什么时候可能跑不起来

- 没有 vendor/runtime
- 没有模型权重
- `ref_audio_path` 不存在
- 服务配置里的资源路径不对
- 本机音频输出设备不可用

## 当前公开版注意事项

- 仓库默认不附带模型、参考音频和 vendor/runtime
- 你需要自己准备 `services/tts_bridge/config/service.toml` 所引用的资源
- 如果不准备真实 TTS，可以先不启动这个服务，让 runtime 退回控制台 TTS

## GPT-SoVITS 安装与补配

当前公开版已经移除了你私有环境里的 GPT-SoVITS 整合包、模型资源和对应配置，因此要跑真实 TTS，需要自行补齐。

建议步骤：

1. 准备你自己的 GPT-SoVITS 运行时目录。
2. 确认运行时里的 Python 和 API 入口脚本可独立启动。
3. 准备你的模型配置文件，例如 `tts_infer.yaml`。
4. 准备参考音频，并确认路径可访问。
5. 修改 `services/tts_bridge/config/service.toml`。

当前最关键的配置项：

- `[vendor].python_executable`
  - 指向 GPT-SoVITS 运行时里的 Python
- `[vendor].entry_script`
  - 指向 GPT-SoVITS 的 API 入口脚本
- `[vendor].tts_config_path`
  - 指向你的模型配置文件
- `[vendor].device`
  - `cuda` 或 `cpu`，取决于你的本机环境
- `[preset].ref_audio_path`
  - 指向参考音频

如果这些字段仍然保留公开版里的示例路径，服务通常会出现以下问题：

- 启动失败
- vendor 进程拉不起来
- 能启动但无法合成
- 播放链路没有声音

## 字幕同步

当字幕同步开启时：

- TTS 播放侧会向 `obs_bridge` 推送字幕事件
- 有播放进度时逐字同步
- 没有细粒度进度时，会退化为估算式逐字推进

## 与其他 sidecar 的关系

- `body_service`
  - 接收 speaking 生命周期事件，但不阻塞 TTS 主链
- `lipsync_bridge`
  - 接收句级音频镜像通知，但镜像失败不反压主播放

这也是公开版当前比较重要的边界设计：执行层 sidecar 不应拖慢 TTS 主播出。
