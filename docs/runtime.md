# 运行时主链

## 主入口

入口文件：

- [uchat/cli.py](../uchat/cli.py)

它负责：

1. 加载配置
2. 构建 memory / model router / adapters
3. 创建 `RuntimeOrchestrator`
4. 进入控制台或直播轮询循环

## runtime 分层

### `RuntimeOrchestrator`

- 场景注册
- 前后台场景路由
- 把事件交给具体 `SessionRuntime`

### `SessionRuntime`

- 单个 scene 的 turn 编排核心
- normalize
- scene_state_update
- identity_resolve
- moderation
- memory ingest / context build
- timing gate
- prompt render
- LLM stream
- output dispatch

### `RuntimeOutputManager`

- 把句子分片转成 `OutputTask`
- 调度 TTS / OBS / text 输出
- 处理中断、取消和 generation 生命周期

## 典型流程

控制台输入：

```text
用户输入
-> NormalizedEvent
-> SessionRuntime
-> prompt + LLM
-> sentence chunks
-> RuntimeOutputManager
-> TTS / OBS / text
```

## 调试建议

- 看 `debug/` 工件
- 看 `message_chain.json`
- 看控制台 timeline / conversation 视图
