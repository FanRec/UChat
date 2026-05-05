# body_service 进度

更新时间：2026-05-04

## 已完成

- 已把 `body_service` 从 skeleton 升级为真实 FastAPI 服务。
- 已补齐：
  - `config.py`
  - `models.py`
  - `state_store.py`
  - `intent_fuser.py`
  - `idle_engine.py`
  - `debug_view.py`
  - `backend/mock.py`
  - `backend/vts.py`
- 已固定最小 API：
  - `GET /health`
  - `GET /v1/body/state`
  - `POST /v1/body/command`
  - `POST /v1/body/speech-event`
  - `POST /v1/body/cancel-trace`
- 已把 `speech_plan + sync_to_audio=true` 收口成“先挂起，等真实 `segment_start` 再执行”，避免 body 比音频更早动。
- 已为 `body_service` 增加独立 debug JSON 落盘：
  - `debug/body_service/latest_state.json`
  - `debug/body_service/*_command.json`
  - `debug/body_service/*_speech_event.json`
  - `debug/body_service/*_cancel_trace.json`
- 已补 Rich 彩色后台分区日志：
  - `Body Command`
  - `Speech Event`
  - `VTS Hotkey`
  - `Body Startup`
  - `VTS Tracking`
- 已为 `Hiyori_A` 补第一版 profile，并显式对齐到它当前已有的 3 个 hotkey。
- 已新增 `services.body_service.smoke` 作为最小联调脚本。
- 已接通 `tts_bridge -> body_service` 播放事件通知第一版：
  - 当前 `tts_bridge` 会在真实播放开始/结束时发 `segment_start / segment_complete`
  - 当前 `cancel_trace / turn_end` 也已和 `body_service` 协同
  - 当前协同仍为 best-effort，不反压 TTS 主链
- 已完成 speaking 动作拟人化第一轮：
  - speaking 期改为 `body_service` 本地连续动作层驱动，不再只靠“更大一点的 idle”
  - 已新增 deterministic speaking signature，按 `trace_id + generation_id + segment_index + text` 稳定生成轻随机动作风格
  - 已新增 speaking / cooldown envelope，本地连续叠加更明显的 yaw / pitch / roll / sway
  - 当前 speaking 增强不依赖 `segment_progress`，只靠真实已接通的 `segment_start / segment_complete / turn_end / cancel_trace`
- 已完成 idle / eye tracking 增强第二轮：
  - 已确认 `Hiyori_A` 当前映射了 `EyeRightX / EyeRightY`
  - 已在 profile 中启用 `eye_x / eye_y`，为 idle 与 speaking 都加入平滑眼神移动
  - 已进一步提高 idle 幅度与“呼吸感”头部起伏，让待机期更容易被肉眼感觉到
- 已完成 speaking 姿态锚点增强第三轮：
  - speaking 期不再主要围绕中心左右摆，而是会先建立偏侧姿态锚点
  - 随后在锚点附近做更丝滑的漂移和 glance，更接近 Neuro-sama 风格的“有重心”观感
- 已完成 tracking 平滑层与高频更新第四轮：
  - `body_service` 新增本地 tracking 插值层，不再把目标参数直接低频硬推到 VTS
  - `Hiyori_A` 当前 profile 已提升到 `24Hz`
  - idle / speaking / event trigger 现在使用不同 alpha，继续压低“像卡顿一样的一格一格跳”
- 已完成 idle wander 与分句 bridge 第五轮：
  - 非说话期现在会周期性更换观察方向，头部和眼神会一起慢慢看向别处
  - speaking 分句结束后会先进入 `bridged_pause`，避免动作气势突然归零
- 已完成 turn-level speaking continuity 第六轮：
  - speaking 同一轮内当前会尽量复用 turn-level pose signature，而不是每个分句都重新生成一套主姿态
  - `Hiyori_A` 当前 profile 已进一步提到 `30Hz`，继续压低低频感
- 已完成 attention / accent 第七轮：
  - 当前 speaking / idle 动作层已进一步拆成 `idle base / idle wander / idle glance / speech accent / speech posture`
  - speaking 每个分句现在会在 `body_service` 本地生成一段 segment-level accent runtime，不依赖新增事件也能在句中做小重音、局部换侧与短停留
  - idle 现在除了慢 wander 外，还会偶发 glance burst，让待机更像“在观察周围”而不是持续均匀漂移
  - tracking 插值已从统一 alpha 改为按 `head / body / eye / smile` 分组平滑，开始落地“眼先动、头后跟、身体最慢跟”
  - 后续补充了一层 speaking `retarget hold` runtime：说话中途可再做一次“眼先切过去、头慢半拍跟、短暂停留”的注意力切换
  - 当前又进一步把 idle 头部基础活动、wander、glance 的头部占比抬高，并让 `segment_start / segment_complete` 更少在事件瞬间硬推 tracking，改为更多交给本地连续层推进，以继续压低“被拉一下”的体感
  - 当前又新增一层 `idle attention` runtime：待机时会周期性建立更明确的观察目标，并在更长的驻留窗口内让头部、眼神一起看向某处，而不再只是“眼球扫一下”或“慢漂”
- 已完成 staged idle / hotkey 辅助第八轮：
  - 当前已在 `body_service` 内新增 profile 驱动的 staged idle 编排层，不再只靠连续 tracking 数值放大
  - 当前 idle tick 会在非 speaking 期调度更明确的待机表演 stage，并叠加到原有 `idle base / wander / attention / glance` 连续层上
  - 当前 stage 已具备 speaking 后抑制窗口、stage cooldown、runtime 统一开关与独立 debug summary
  - 当前 `Hiyori_A` 已新增三类 stage：
    - `cute_sway`
    - `peek_attention`
    - `bounce_emphasis`
  - 当前 hotkey 只允许作为 idle stage 的受控增强器，仍不恢复 `My Animation 1/2/3` 为 speaking motion cycle 主路径
  - 当前已新增 staged idle 回归测试，覆盖：
    - idle 可进入 stage
    - speaking / speaking 后抑制
    - stage cooldown
    - hotkey 允禁边界
    - idle refresh debug summary
- 已完成 staged idle 真人操纵感收口第九轮：
  - 当前已把 idle stage 从“连续循环表演”进一步收口到“持姿 -> 短 burst -> 微修正 -> 回稳”的时序
  - 当前 stage 活跃时会主动压低 `idle base / wander / attention / glance`，避免多层同时持续转动而显得像自动待机动画
  - 当前 profile 也已降低 stage 频率、拉长持姿、收紧 burst 密度，让待机更接近真人 Vtuber 扶着姿态、偶尔修一下的表演手感
- 已完成 idle 幅度与偏头观察放大第十轮：
  - 当前继续放大了 `cute_sway / peek_attention / bounce_emphasis` 的 head-led 表现
  - 当前 `peek_attention` 已更明确地表现为“明显偏头观察并驻留”，不再主要停留在眼先看过去
  - 当前目标继续收口为：在保留真人操纵感的前提下，让 idle 在 VTS 里肉眼上更明显、更大摇大摆、更像在营业
- 已完成 idle 连续性收口第十一轮：
  - 当前已把 idle stage 内更容易造成“抽一下”的短脉冲进一步改成更平滑的 bump 过渡
  - 当前 stage 活跃时保留了更多 base 连续层，降低 stage 进出时的切层感
  - 当前 profile 也已改成更长时长、更久持姿、更柔回收，并继续下调 `bounce_emphasis` 的 hotkey 参与比例
  - 当前优先级已明确变为：先连续丝滑，再保留明显偏头与观察感

## 当前策略

- 这轮只做 `body_service / VTS / Live2D` 侧，不改 runtime 主编排。
- 这轮不启用 planner / emotion / LLM 动作控制。
- 当前已接入 `tts_bridge -> body_service` 的异步通知层，但仍不把 body 变成 TTS 主播放链阻塞项。
- 当前 speaking 期不恢复 `My Animation 1/2/3` 作为 motion cycle 主路径，继续避让嘴型。
- 当前 speaking 的句内表现优先依靠本地 accent 编排，而不是继续堆高频 sway 频率。
- 当前 idle 的主方向已从“继续纯 tracking 放大”切换为“tracking 连续层 + staged idle 编排 + 少量 hotkey 辅助”。
- 当前 idle 的更细方向已进一步收口为“真人操纵感优先”，而不是“自动待机动画更热闹”。

## 当前限制

- 目前 `body_service` 还没有正式挂入 runtime 主链，仍未接 `runtime -> body_service` 的低频语义命令层。
- `VTS backend` 当前优先走 hotkey + tracking injection，还没有做更细的 expression activation 或 custom parameter 策略。
- 目前 profile 是围绕 `Hiyori_A` 的现状能力写的；后续换模型时仍需要独立 profile 调参。
- 目前 speaking reactive 以句级/播放事件级为主，没有做 viseme 级控制，也没有做 `segment_progress` 级正式接线。
- `Hiyori_A` 现有 `My Animation 1/2/3` 会写嘴巴参数，因此当前已先把 speaking motion cycle 置空，避免和 VTS lipsync 冲突。
- 当前 speaking 拟人化虽然已落地，但仍是 `Hiyori_A + VTS tracking` 定向调参结果；换模型时不应直接照搬幅度。
- 当前 speaking accent 仍然是基于句长估算的本地时序，不是真实音素/分词/播放进度级对齐；它解决的是“更灵、更像人在操纵”的一部分，不是严格 lipsync 或精确语音节拍控制。
- 当前 idle 已连续增强多轮，但最终“足够灵动、不无聊”的收口仍然高度依赖真实 VTS 观感，不应只按参数规模判断已完成。
- 当前 staged idle 已落地第一版，但 stage 的最佳频率、时长、视觉密度和 hotkey 触发比例仍需真实 VTS 长时 smoke 验证。
- 当前真实 VTS 观感结论已经更明确：
  - speaking 第一版当前可暂时“看过眼”
  - idle 仅靠连续 tracking 放大已接近收益递减
  - 当前已转向 staged idle / 少量 hotkey 辅助路线，但仍需继续用真实 VTS 观感确认是否足够“大开大合、可爱、在营业”

## 下一步

- 当前嘴型同步已先走 `tts_bridge -> lipsync_bridge -> VTS` 旁路镜像路线；当前播放事件通知层也已接到 `body_service`。
- 下一步重点应继续做真实 VTS 长时 smoke 和 profile 调参，观察：
  - 长句连续 speaking 时是否仍足够自然
  - 不同句长/不同语速下 cooldown 是否过强或过弱
  - idle 呼吸感与 idle glance 密度是否仍需继续增强或收紧
  - 眼神移动频率和幅度是否需要继续收紧或放开
  - segment accent 的切入时机、停留时长和换侧概率是否还需继续校准
  - retarget hold 的触发窗口、时长和切侧力度是否还需继续校准
  - staged idle 的进入频率、停留时长、回收节奏和 hotkey 参与比例是否还需继续校准
  - `bounce_emphasis` 这类 stage 是否已经足够显眼，还是仍需更强的 stage tracking/hotkey 组合
- `runtime -> body_service` 的低频语义命令层应延后到后续 runtime 重构时再一并接入，而不是现在单独提前做。
- 若下一轮继续反馈“还是不够营业”，应优先继续增强 staged idle 的段落设计与 profile 资源，而不是重新退回只堆同一路 tracking 数值。
