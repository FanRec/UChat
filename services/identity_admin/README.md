# identity_admin

`identity_admin` 是 UChat 的本地身份治理入口服务。

它只负责：

- 发起 challenge
- 消费 challenge
- 手工 bind 账号到已有 `person_id`
- rename canonical `display_name`
- 查询 account / person 当前 identity 状态

它不负责：

- runtime turn 编排
- 平台协议接入
- prompt 生成
- moderation
- LTMem 记忆召回

第一版明确只走本地管理 API，不进入弹幕，也不进入平台私聊。
