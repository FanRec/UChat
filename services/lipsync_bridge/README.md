# lipsync_bridge

`lipsync_bridge` 是一个独立 sidecar，用来把 `tts_bridge` 已经准备播放的句级音频旁路复制到 `VTube Studio` 可选的虚拟输入设备。

## 目标

- 不改 `tts_bridge` 主播放链单输出事实
- 镜像失败不影响 TTS 主播出、首句延迟、句间延迟和字幕同步
- 默认只做句级镜像，不在本轮重写 `tts_bridge` 的流式主播放算法

## 当前接口

- `GET /health`
- `GET /v1/lipsync/devices`
- `POST /v1/lipsync/mirror`
- `POST /v1/lipsync/cancel-trace`
- `POST /v1/lipsync/turn-end`

## 当前行为

- `tts_bridge` 会在句级音频资产就绪、准备播放时，best-effort 通知本服务
- batch/WAV 路径默认传本地 `audio_path`
- streaming/raw PCM 路径默认以内联 `base64 PCM` 发送
- sidecar 自己排队播放镜像音频；忙时可丢弃，不反压主链
- Windows 当前会先尝试同名设备中的 `WASAPI`，若打开失败则自动回退到同名的下一个候选（当前实测常落在 `DirectSound`）
- PCM 与单声道 16-bit WAV 当前会按目标设备采样率自动重采样后再播，避免 `32000 -> 48000` 一类采样率不兼容

## 使用说明

1. 在 `services/lipsync_bridge/config/service.toml` 里把 `[output].device` 设成虚拟线的播放端名称  
   例如 `CABLE Input (VB-Audio Virtual Cable)`
2. 在 VTS 的 lipsync 设置里把麦克风切到对应的录音端  
   例如 `CABLE Output (VB-Audio Virtual Cable)`
3. 启动服务：

```powershell
uv run python -m services.lipsync_bridge.main --serve
```

## 注意

- 这不是“把 raw audio stream 直接喂给 VTS plugin API”  
  当前公开能力边界仍是“VTS 自己听一个输入设备”。
- 本服务当前只作为旁路镜像主路线。
- “旁路监听/loopback” 只保留为后续诊断兜底思路，不作为当前默认实现。
- Windows 里真正应该观察电平的是 `CABLE Output (VB-Audio Virtual Cable)`，不是 `CABLE Input`。
- 如果耳机里听到双份声音，优先检查 `CABLE Output` 是否开启了“侦听此设备”或被其他软件做了 monitor/回听。
