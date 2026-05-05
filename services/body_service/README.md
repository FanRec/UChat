# body_service

`body_service` 现在是一个真实的身体执行服务，负责把句级/轮级身体意图和 `tts_bridge` 的播放生命周期融合成可执行的 Live2D/VTube Studio 动作。

## 这轮目标

- 保持 `runtime / tts_bridge` 主链不被 body 逻辑反压
- 先把身体执行边界补成正式服务，而不是把 Live2D 代码倒灌回 runtime
- 第一阶段优先支持 `mock` 和 `VTube Studio` 两种 backend
- 口型不在本服务逐帧生成，优先交给 VTS 自身 lipsync；本服务负责 speaking lifecycle、被动动作和 idle

## 当前接口

- `GET /health`
- `GET /v1/body/state`
- `POST /v1/body/command`
- `POST /v1/body/speech-event`
- `POST /v1/body/cancel-trace`

## 当前命令类型

- `speech_plan`
- `expression`
- `motion`
- `set_baseline`
- `turn_end`
- `clear`

## 当前 speech event 类型

- `segment_start`
- `segment_progress`
- `segment_complete`
- `turn_end`
- `clear`

## 关键边界

- `speech_plan + sync_to_audio = true` 时只先挂起，不立即触发动作；等真实 `segment_start` 到达再落地。
- Idle / passive motion 全部留在 `body_service` 本地轻循环，不把高频控制推回 runtime。
- speaking 期的“活人感”现在主要由 `body_service` 本地连续动作层生成：
  - 基于 `trace_id + generation_id + segment_index + text` 生成稳定 speaking signature
  - 在本地 idle tick 上持续叠加 posture / sway / eye glance / segment accent
  - `segment_complete` 后走 cooldown envelope 自然回落
- 当前本地动作层已进一步区分：
  - `idle base`
  - `idle wander`
  - `idle attention`
  - `idle glance`
  - `idle stage`
  - `speech posture`
  - `speech accent`
- tracking 输出现在会先经过 `body_service` 本地插值层，再注入 VTS：
  - `Hiyori_A` 当前 profile 已把 tick 提到 `30Hz`
  - 当前不再只用一组 tracking alpha，而是按 `head / body / eye / smile` 分组平滑
  - 目的是减少“低频跳动”和肉眼可见卡顿，并开始做出“眼先动、头后跟、身体最慢跟”的层级惯性
- 非说话期当前还会启用 idle wander：
  - 每隔数秒换一个“观察方向”
  - 头部和眼神会一起慢慢看向别处
  - 目的是让待机期也保持“像在观察周围”的存在感
- 非说话期当前还会启用 idle attention：
  - 周期性建立更明确的观察目标
  - 头部和眼神会在更长一点的窗口里一起看向该目标
  - 目的是让待机更像“角色真的在看什么”，而不只是眼球偏一下或参数慢漂
- 非说话期当前还会偶发 idle glance burst：
  - 先快速看向一个方向
  - 再带一点头部偏转
  - 最后柔和回收
  - 目的是避免待机期只剩均匀呼吸和小幅巡航
- 非说话期现在还会进入 staged idle：
  - 在本地 idle tick 上周期性挑选一段更明确的待机表演 stage
  - stage 期间保留 tracking 连续层，但会叠加更大幅的偏头、停留、回弹和回收
  - 目标是让待机更像“虚拟主播正在营业”，而不是只有底层 tracking 在轻微波动
- 最近又继续加强了 idle 的头部存在感：
  - idle base 不再只是轻微慢漂，而是加入更明显的头部活动感
  - wander 与 glance 也提高了头部参与度，尽量避免“只有眼睛在动、头几乎不动”的体感
  - 后续又加入 `idle attention` 与更表演化的 idle performance 层，但当前用户侧实测仍认为待机观感不够大、不够像在营业
- 当前最新调整是：
  - 不再优先继续堆同一路 tracking 数值
  - 改为“tracking 连续层 + staged idle 编排 + 少量受控 hotkey 辅助”的分层路线
  - hotkey 只允许在 idle stage 中低频增强，不回到 speaking 主路径
- 当前虽然已接入 `tts_bridge -> body_service` 的 best-effort 播放事件通知层，但仍没有把 body 强行接入 `runtime` 主编排，也没有把 body 变成 `tts_bridge` 主播放链阻塞项。
- 目的是继续把 TTS 首句延迟、句间延迟和字幕稳定性风险隔离开；`runtime -> body_service` 应留到后续 runtime 重构时再一并接入。

## 配置

主配置：`services/body_service/config/service.toml`

模型 profile：`services/body_service/config/body_profiles/*.toml`

默认 profile：`hiyori_vts.toml`

## 启动

```powershell
uv run python -m services.body_service.main --serve
```

最小烟测：

```powershell
uv run python -m services.body_service.smoke
```

## Hiyori_A 当前策略

- 现有 3 个 VTS hotkey：
  - `My Animation 1`
  - `My Animation 2`
  - `My Animation 3`
- 表情层优先走 tracking bias（如 `MouthSmile`、`FaceAngle*` 的轻偏置）
- 明显动作优先走 hotkey
- 被动动作/idle 优先走 tracking parameter injection
- 当前 idle stage 可选少量 hotkey 增强：
  - 仍只在 idle 中触发
  - speaking / bridged_pause / cooldown 期间会被压制
  - 目的是补足 `Hiyori_A + VTS tracking` 在大幅待机表演上的可见度上限
- 当前已默认关闭 speaking 时的 hotkey 动作轮换：
  - 因为 `My Animation 1/2/3` 会写 `ParamMouthOpenY`
  - 在 VTS 内建 lipsync 路线下会直接和嘴型打架
- 当前 speaking motion 的主路径改为 tracking injection 连续层，不再依赖 speaking hotkey cycle：
  - 更明显的偏头与轻身体 sway
  - 起句 accent + 句末 settle
  - speaking 期间的眼神微扫
  - 带偏侧锚点的姿态停留与丝滑漂移，更接近 Neuro-sama 那种“有重心”的说话观感
  - speaking 句内局部 accent：可选换侧、短停留、眼先动再头跟
  - speaking 中途 retarget hold：再做一次注意力切换，进一步降低“整句一直围着同一侧摆”的体感
  - 同一 trace/segment 可复现，便于 debug/replay

## Hiyori_A 可用眼神输入

- 当前已确认 `hiyori.vtube.json` 映射：
  - `EyeRightX -> ParamEyeBallX`
  - `EyeRightY -> ParamEyeBallY`
- 因此 profile 当前已启用：
  - `eye_x = "EyeRightX"`
  - `eye_y = "EyeRightY"`
- 这轮 idle / speaking 都会给眼球加入轻微平滑扫视，但仍不会碰嘴型相关输入。

## Speaking 调参点

- speaking 幅度参数集中在 `speech_reactive`：
  - `speaking_yaw_range`
  - `speaking_pitch_range`
  - `speaking_roll_range`
  - `sway_*`
- 轻随机签名参数：
  - `signature_scale_min/max`
  - `onset_emphasis_min/max`
  - `settle_strength_min/max`
  - `smile_boost_min/max`
- 收尾回落参数：
  - `segment_complete_hold_ms`
  - `cooldown_falloff_ms`
- 句内本地编排参数：
  - `segment_duration_*`
  - `accent_*`
  - `figure8_mix_*`
- idle 观察感参数：
  - `glance_probability`
  - `glance_*`

当前真实联调不依赖 `segment_progress`；只靠 `segment_start / segment_complete / turn_end / cancel_trace` 即可驱动 speaking -> cooldown -> idle 的完整表现链。
当前 speaking 在句间还会先进 `bridged_pause`，避免分句一结束动作就突然掉光。
同一轮 speaking 当前也会尽量复用 turn-level pose signature，而不是每个分句都重建一套新姿态。
当前 speaking 句内新增的 accent runtime 也是本地估算时序，不需要扩张 `tts_bridge -> body_service` 事件集合。
最近又把 `segment_start / segment_complete` 的 tracking 硬注入进一步收紧，让句间更多靠本地连续层自然推进，以压低“虽然连续，但像被拉一下”的感觉。
当前最新结论是：在 `Hiyori_A + VTS tracking` 这条路线下，speaking 第一版暂时可接受，但 idle 幅度与表演感只靠连续 tracking 已接近收益递减；当前已经转向更明确的 staged idle 与少量 hotkey 辅助路线。

## staged idle 当前边界

- staged idle 当前完全留在 `body_service` 本地调度，不改 runtime 主编排。
- stage 只在 `speech_phase = idle` 时进入：
  - `speaking`
  - `bridged_pause`
  - `cooldown`
  都会压制或清空 idle stage。
- stage 调度当前具备：
  - speaking 后抑制窗口
  - stage cooldown
  - profile 驱动的 stage 选择与时长
  - stage 内 expression / motion / tracking gain
- hotkey 只作为 idle stage 的低频增强器：
  - profile 可配置是否允许
  - runtime 可统一禁用
  - 不会恢复为 speaking motion cycle 主路径

## staged idle 配置

- profile 级运行时开关：`[idle_stage_runtime]`
  - `enabled`
  - `cooldown_min_s / cooldown_max_s`
  - `suppression_after_speaking_s`
  - `allow_hotkey`
- stage 定义：`[idle_stages.<name>]`
  - `pattern = sway / peek / bounce`
  - `expression`
  - `motion`
  - `weight`
  - `duration_*`
  - `hold_ratio`
  - `head_* / eye_* / smile_boost`
  - `tracking_gain`
  - `hotkey_probability`

当前 `Hiyori_A` 已补三类 stage：

- `cute_sway`
- `peek_attention`
- `bounce_emphasis`

设计上它们是 profile 能力，不是 `service.py` 里写死的 `Hiyori_A` 特判。

## 这轮对 idle 的新判断

当前又进一步确认：如果目标是“像真人 Vtuber 正在手上操纵皮套”，那么 idle 不能主要表现成：

- 持续均匀循环
- 多层同时一直在动
- 每一刻都在自动表演

这更像自动待机动画，而不像真人在扶着一个姿态。

因此当前 idle stage 的设计原则已进一步调整为：

- 大部分时间先持住一个姿态
- 中间只做少量短 burst
- burst 后做一两次微修正
- 最后回到新的稳态或自然收回
- stage 活跃时会主动压低 `idle base / wander / attention / glance` 的存在感

当前路线更接近：

- `持姿`
- `微修正`
- `短 attention burst`
- `回稳`

而不是“持续摇、持续看、持续转”。

后续又继续把 idle stage 往“更大摇大摆、偏头观察更明显”的方向推了一轮：

- `cute_sway` 的偏头持姿更大、驻留更久
- `peek_attention` 不再只是眼先过去，而是头最终也会明显偏过去并停住
- `bounce_emphasis` 继续保持短 burst，但头部主导权更高

当前目标不是“处处都在动”，而是“该偏的时候明显偏，该看的时候明显在看”。

后续真实观感又进一步暴露出一个更具体的问题：

- 幅度和偏头是够了
- 但局部会有“抽一下”“顿一下”的感觉

因此当前 idle 的优先级又进一步调整为：

- 先保证连续丝滑
- 再在连续前提下保留明显偏头与观察感

当前为此做的收口包括：

- stage 内短 burst 从更硬的脉冲感改成更平滑的 bump 窗口
- stage 活跃时不再把 base 层压得过狠，避免 stage 进出像切层
- stage 时长更长、持姿更久、回收更柔
- `bounce_emphasis` 的 hotkey 参与比例进一步下调，先让 tracking 连续层承担主体流畅度

## 为什么 tracking 用 `FaceAngleX/Y/Z` 而不是直接写 `ParamAngleX/Y/Z`

VTS 官方 plugin API 的稳定注入面是 tracking/custom parameter。  
所以这轮实现里，profile 先描述逻辑量（`head_x/head_y/head_z/smile`），再映射到 VTS 的 tracking 输入（例如 `FaceAngleX`、`MouthSmile`）。  
这比把 `ParamAngleX` 之类 Live2D 参数名直接暴露给服务层更稳，也更利于以后切换别的身体 backend。
