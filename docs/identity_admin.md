# 身份治理模块

对应目录：

- [services/identity_admin](../services/identity_admin)

## 它负责什么

- challenge 发起
- challenge 消费
- 手工绑定账号和 person
- rename canonical display name
- 查询 identity 状态

## 如何启动

```bash
uv run uvicorn services.identity_admin.main:app --host 127.0.0.1 --port 8111
```

## 依赖

- `config/app.toml` 里的 `identity_store`

如果是 sqlite：

- 首次启动会自动创建数据库

## 适合什么时候用

- 多平台账号要归并到同一人
- 需要手工治理 display name / person_id
