# 身份治理模块

对应目录：

- [services/identity_admin](../services/identity_admin)

## 它负责什么

- 发起 challenge
- 消费 challenge
- 手工绑定账号与 person
- 重命名 canonical display name
- 查询 identity 状态

## 如何启动

```powershell
uv run python -m services.identity_admin.main serve
```

## 它依赖什么

- `config/app.toml` 中的 `[identity_store]`

如果使用 SQLite：

- 首次启动会自动创建数据库文件
- 默认路径是 `data/identity.sqlite3`

## 适合什么时候用

- 多平台账号要归并到同一个 person
- 需要人工治理 display name / person_id
- 你不想把“身份修正”逻辑硬写进 runtime 主链
