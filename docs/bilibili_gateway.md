# Bilibili 网关

对应目录：

- [services/bilibili_gateway](../services/bilibili_gateway)

## 它负责什么

- 连接直播房间或离线事件源
- 标准化直播事件
- 做去重、轻量聚合和基础风险标签
- 向 core runtime 提供结构化输入事件

## 它不负责什么

- 最终回复决策
- 角色身份真源
- 最终 moderation 裁决
- TTS、字幕或身体执行

## 如何启动

```powershell
uv run python -m services.bilibili_gateway.main serve
```

## 真实登录态

需要在本地 `.env` 提供：

```dotenv
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_BUVID3=
BILIBILI_DEDEUSERID=
```

## 联调建议

建议顺序：

1. 先确认网关自己能启动。
2. 再确认 `config/app.toml` 里的 `[services.platform.bilibili].url` 指向它。
3. 再启动 `uchat.cli` 观察 runtime 是否开始轮询直播事件。

如果你不想一上来就连真实 live，可以优先使用：

- `mock_mode`
- `offline_test`
- `/v1/bilibili/test-events`

这样更容易先验证“结构化事件 -> runtime 主链”是否通。
