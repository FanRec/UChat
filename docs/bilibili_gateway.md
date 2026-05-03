# Bilibili 网关

对应目录：

- [services/bilibili_gateway](../services/bilibili_gateway)

## 它负责什么

- 连接直播房间
- 标准化直播事件
- 去重 / 聚合 / 轻量风险标签
- 向 core runtime 提供结构化事件

## 它不负责什么

- 最终回复决策
- 身份真源
- 最终 moderation 裁决

## 如何启动

```bash
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

先用：

- `mock_mode`
- `offline_test`
- `/v1/bilibili/test-events`

确认结构化主链没问题后，再接真实 live。
