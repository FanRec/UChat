# identity_admin

`identity_admin` 是本地身份治理入口。

## 它负责什么

- 发起 challenge
- 消费 challenge
- 手工 bind 账号到已有 `person_id`
- rename canonical `display_name`
- 查询 account / person identity 状态

## 它不负责什么

- 不负责 runtime turn 编排
- 不负责平台协议接入
- 不负责 prompt 生成
- 不负责 moderation

## 启动

```bash
uv run uvicorn services.identity_admin.main:app --host 127.0.0.1 --port 8111
```

## 配置

主要依赖：

- `config/app.toml`
- `services/identity_admin/config/service.toml`

如果 `identity_store.store_type = "sqlite"`，首次启动会自动创建本地数据库。
