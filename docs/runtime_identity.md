# 角色与身份说明

公开版里最容易混淆的是两个概念：`runtime identity` 和 `identity store`。

## 1. runtime identity

来源：

- `config/app.toml` -> `runtime.identity`

这是一段角色设定文本，会进入 prompt，影响模型的说话风格、人格感和表达方式。

它不是数据库里的用户身份，也不是某个固定 `person_id`。

公开仓库里当前只保留了示例 identity，使用者应自行替换成自己的角色设定。

## 2. identity store

来源：

- `config/app.toml` -> `[identity_store]`

默认示例：

```toml
store_type = "sqlite"
sqlite_path = "data/identity.sqlite3"
default_console_person_id = ""
```

这表示：

- 启动时使用 SQLite 身份库
- 如果数据库文件不存在，会自动创建
- 控制台输入不会默认绑定到某个固定人物，因为 `default_console_person_id` 为空

## 控制台输入会发生什么

控制台输入时：

- 平台会被标记为 `console`
- 仍会经过 identity 解析流程
- 但不会自动注入某个固定真实人物

所以当前默认是：

- 会有“角色 prompt”
- 不会默认有“预绑定的人物身份”

## 什么时候需要 `identity_admin`

当你开始处理真实平台账号归并、人工绑定和 display name 治理时，再启动 `identity_admin` 会更合适。它负责治理身份数据，而不是替代 prompt 里的角色设定。
