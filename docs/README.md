# 文档导航

<p>
  <a href="../README.md">中文首页</a> | <a href="../README.en.md">English Home</a> | <a href="./en/README.md">English Docs</a>
</p>

这个 `docs/` 目录面向公开版 `UChat_Public`，目标是帮助你快速理解当前代码结构、启动路径和服务边界。

## 推荐阅读顺序

1. 根目录 [README.md](../README.md)
2. [项目结构与运行方式](project_structure_and_run.md)
3. [配置说明](configuration.md)
4. [运行时主链](runtime.md)
5. [角色与身份说明](runtime_identity.md)
6. [TTS 模块](tts_bridge.md)
7. [OBS 字幕模块](obs_bridge.md)
8. [Bilibili 网关](bilibili_gateway.md)
9. [身份治理模块](identity_admin.md)
10. [身体表现模块](body_service.md)

## 阅读建议

- 如果你第一次接触这个仓库，先看前 1 到 4 篇。
- 如果你只是想跑起来，优先看启动方式和配置说明。
- 如果你正在接某个 sidecar，再进入对应 `docs/*.md` 和 `services/*/README.md`。

## 文档口径

- 这里优先描述“公开版当前真实存在的能力边界”。
- 某些服务目录下还保留了更细的进度文档和技术记录，它们适合在联调该服务时再看。
- 公开版文档会尽量避免引用私有环境参数、私人资源路径或内部运维约定。
