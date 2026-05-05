# lipsync_bridge 进度

更新时间：2026-05-04

## 已完成

- 已新增独立 `lipsync_bridge` sidecar 服务骨架与真实 HTTP 接口。
- 已支持句级镜像：
  - batch/WAV 走本地 `audio_path`
  - streaming/raw PCM 走内联 `base64 PCM`
- 已支持：
  - 设备列表查询
  - trace 级取消
  - turn_end 接口占位
- 已固定 sidecar 忙时可丢弃，不反压 `tts_bridge`
- 已完成 Windows 实机第一轮联调：
  - 已确认手动 PCM mirror 可进入 `VB-CABLE`
  - 已确认真实 `tts_bridge` mirror 能触发 sidecar 执行
  - 已补设备打开失败后的同名候选回退（当前实测 `WASAPI -> DirectSound`）
  - 已补 PCM / 单声道 16-bit WAV 按目标设备采样率自动重采样

## 当前限制

- 当前是句级镜像，不是样本级严格同步。
- 当前 `tts_bridge` 的 streaming 主路径仍是“整句 PCM 收满后再播”，因此本轮 mirror 也是句级就绪后触发。
- 旁路监听/loopback 仍只保留为诊断兜底方向。
- 当前路线是 `VTS / Live2D` 专项实现，不应误认为通用身体 backend 的长期总方案。

## 下一步

- 嘴型这部分当前已可用；下一步重点转向 `body_service` 与 Live2D/VTS 身体接入剩余部分。
- 如果后续句级镜像观感仍不足，再评估是否值得把 `tts_bridge` 主路径重构到真正 chunk 级播放/镜像共用。
