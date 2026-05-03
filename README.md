# UChat Public

![icon](./README.assets/icon.png)

UChat 是一个面向“数字角色 / AI 主播 / 可视化聊天体”的本地化运行项目样例。

它当前提供的是一条可运行的最小主链：

- 控制台或直播输入
- 记忆与上下文拼装
- LLM 回复生成
- TTS 输出
- OBS 实时字幕
- 身体表现与身份治理的服务边界预留

这个公开版仓库的目标不是“开箱即用的商业级产品”，而是：

1. 给出一套可以本地跑起来的多服务工程骨架
2. 展示 runtime / TTS / OBS / gateway 的协作方式
3. 方便你基于自己的角色、模型和直播场景继续改造

## 当前包含什么

- `uchat/`
  - 核心运行时、输出队列、适配器、身份、moderation、模型路由
- `services/tts_bridge/`
  - TTS bridge、播放调度、字幕同步
- `services/obs_bridge/`
  - OBS Browser Source 实时字幕服务
- `services/bilibili_gateway/`
  - B 站直播输入前处理网关
- `services/identity_admin/`
  - 本地身份治理入口
- `services/body_service/`
  - 身体表现服务边界骨架
- `config/`
  - 主运行配置与模型配置
- `prompts/`
  - Prompt 文件
- `docs/`
  - 项目说明与模块文档

## 当前不包含什么

为了避免隐私和资源分发问题，这个公开版默认不包含：

- 私人 `.env`
- 本地数据库内容
- debug / logs / data 产物
- TTS 模型权重与参考音频
- vendor 运行时目录
- 私人 cookie / token / session

你需要自行准备：

- 模型 API Key
- 如需真实直播输入，准备自己的 B 站登录 cookie
- 如需真实 TTS，准备自己的 GPT-SoVITS vendor/runtime 与模型资源

## 本地运行前准备

### 1. Python 环境

推荐使用 `uv`：

```bash
uv sync
```

或自己创建 Python 3.12 虚拟环境后安装依赖。

### 2. 环境变量

复制：

```bash
cp .env.example .env
```

Windows PowerShell 可手动创建 `.env`。

至少需要填写：

```dotenv
DEEPSEEK_API_KEY=your_api_key_here
```

如果你要联调真实 B 站直播输入，再补：

```dotenv
BILIBILI_SESSDATA=
BILIBILI_BILI_JCT=
BILIBILI_BUVID3=
BILIBILI_DEDEUSERID=
```

### 3. 修改角色配置

`config/app.toml` 中的 `runtime.identity` 目前只是公开示例文本。

请把它替换成你自己的角色设定，不要直接拿示例内容作为最终人格配置。

### 3.1 身份库

默认使用 SQLite 身份库：

- `data/identity.sqlite3`

首次启动时如果文件不存在，会自动创建。

如果你想只做纯控制台测试，也可以把默认控制台 person 继续保持为空：

- `default_console_person_id = ""`

### 4. TTS 资源

当前仓库不附带 TTS 模型、参考音频和 vendor 运行时。

如果你想跑真实 TTS：

1. 准备 GPT-SoVITS vendor/runtime
2. 准备模型配置文件
3. 修改 `services/tts_bridge/config/service.toml`

如果暂时不跑真实 TTS，也可以把 `[services.tts].url` 留空，让主链退回控制台 TTS 适配器。

注意：公开版默认**不附带** TTS 模型、参考音频和 vendor 运行时，它们需要你自己准备。

## 最小启动方式

### 方式一：先只跑主程序

如果你只是想验证 runtime / prompt / LLM 主链：

```bash
uv run python -m uchat.cli
```

这要求：

- `.env` 已配置好 LLM API Key
- `config/models.toml` 可正常读取

### 方式二：跑 OBS 实时字幕

先启动字幕服务：

```bash
uv run python -m services.obs_bridge.main --serve
```

然后在 OBS 里添加 Browser Source：

- URL: `http://127.0.0.1:8104/overlay/`

### 方式三：跑真实 TTS bridge

```bash
uv run python -m services.tts_bridge.main --serve
```

注意：

- 只有当你已经准备好 vendor/runtime 和模型资源时，这一步才有意义
- 否则建议先不启用真实 TTS

### 方式四：跑直播输入网关

```bash
uv run python -m services.bilibili_gateway.main serve
```

真实直播联调前，请先看：

- `services/bilibili_gateway/README.md`
- `services/bilibili_gateway/config/service.toml`

## 推荐调试顺序

1. 先跑 `uchat.cli`，确认 LLM 主链可用
2. 再跑 `obs_bridge`，确认 OBS 字幕可连通
3. 再接 `tts_bridge`
4. 最后再接 `bilibili_gateway`

这样最容易定位问题，不会一开始就被多服务耦合卡住。

## 文档入口

优先从这里看：

- [docs/README.md](docs/README.md)
- [services/obs_bridge/README.md](services/obs_bridge/README.md)
- [services/tts_bridge/README.md](services/tts_bridge/README.md)
- [services/bilibili_gateway/README.md](services/bilibili_gateway/README.md)
- [services/identity_admin/README.md](services/identity_admin/README.md)

## 隐私与公开版说明

这个仓库是公开版整理目录。

默认约束：

- 不提交 `.env`
- 不提交数据库
- 不提交日志/debug/data
- 不提交私人 TTS 模型和参考音频
- 不提交私人 cookie/token

如果你继续在这个目录里开发，建议保持这些约束不变。
