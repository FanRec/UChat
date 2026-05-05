# 身体表现模块

对应目录：

- [services/body_service](../services/body_service)

## 当前状态

`body_service` 现在已经是一个真实 sidecar，而不只是占位骨架。

它的职责是把句级或轮级身体意图、TTS 播放生命周期和本地 idle/speaking 行为融合成可执行的身体表现，并把具体执行细节隔离在独立服务中。

## 它负责什么

- 接收身体命令和 speaking 生命周期事件
- 维护本地 body state
- 驱动 `mock` 或 `VTube Studio` backend
- 在本地做 idle / passive motion / speaking 连续层
- 支持 `cancel-trace`、`turn_end`、`clear`

## 它不负责什么

- 回复生成
- 平台输入协议
- runtime 主链里的回复决策
- 逐帧口型生成

## 当前边界

- 口型优先交给 VTS 自身 lipsync 或独立 `lipsync_bridge`
- `body_service` 不应反压 runtime 或 TTS 主链
- 高频 idle 和连续 tracking 行为都尽量留在本地服务循环里

## 如何启动

```powershell
uv run python -m services.body_service.main --serve
```

最小烟测：

```powershell
uv run python -m services.body_service.smoke
```

## 配置入口

- 主配置：`services/body_service/config/service.toml`
- profile：`services/body_service/config/body_profiles/*.toml`

如果你想接真实 VTube Studio，建议继续配合该服务目录下的详细 README 一起看。
