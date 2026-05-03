# 角色与身份说明

公开版里有两个容易混淆的概念：

## 1. runtime identity

来源：

- `config/app.toml` -> `runtime.identity`

这是一段角色设定文本，会进入 prompt，影响模型说话风格和角色人格。

它不是数据库里的用户身份，也不是某个固定 `person_id`。

公开仓库里当前只保留了**示例 identity**，使用者应自行替换成自己的角色设定。

## 2. identity store

来源：

- `config/app.toml` -> `[identity_store]`

默认配置：

```toml
store_type = "sqlite"
sqlite_path = "data/identity.sqlite3"
default_console_person_id = ""
```

这表示：

- 启动时会使用 SQLite 身份库
- 如果数据库文件不存在，会自动创建
- 控制台输入不会自动绑定到某个固定人物，因为 `default_console_person_id` 为空

## 控制台输入会发生什么

控制台输入时：

- 事件平台会被标记为 `console`
- 会进入 identity 解析流程
- 但不会自动注入某个固定真实人物

所以：

- 会有“角色 prompt”
- 不会默认有“预绑定的人物身份”
