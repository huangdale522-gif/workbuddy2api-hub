# 续维护说明

本仓库是 `workbuddy2api-hub` 的社区续维护分支，维护基线与原作者最后公开的稳定版一致：

- 基线版本：`v2.1.1`
- 基线提交：`f5f15d33d401e590b3869017beb480a54a91de7a`
- 原项目：`ardeyouxipianyi/workbuddy2api-hub`
- 续维护仓库：`huangdale522-gif/workbuddy2api-hub`

原作者的上游公开仓库目前已不可访问。本仓库保留原项目的 MIT 许可、作者署名、变更记录与贡献者致谢，但不再声称自己是原作者官方发布物。

## 维护原则

1. 不重写历史，不强制推送 `main`，不覆盖已有版本标签。
2. 所有代码改动通过独立分支和 Pull Request 提交，至少跑完整的 `tests.yml` 矩阵。
3. 保留作者建立的发布门禁：标签版本必须等于源码版本，完整测试通过后才允许创建 Draft Release。
4. 稳定版只从 `vX.Y.Z` 标签发布；正式 Release 发布后自动构建 `linux/amd64` 与 `linux/arm64` 镜像。
5. 部署、更新检查、便携版更新和容器镜像默认指向本续维护仓库，避免依赖已失效的上游地址。
6. 上游重新公开或授权同步时，先逐提交审查，再通过 PR 合入；不恢复无人审查的自动强同步。

## 发布流程

1. 更新 `wb_proxy.py` 的两处版本号、`wrt/openwrt/workbuddy2api/Makefile` 的 `PKG_VERSION`、README 徽章与 `docs/CHANGELOG.md`。
2. 运行 `python tests/run_all.py --jobs 4`，并完成贡献指南要求的真实链路冒烟。
3. 推送 `vX.Y.Z` 标签，等待 `release` 工作流生成 Draft Release。
4. 检查发布说明与资产，手动 Publish Release。
5. `Publish Docker image` 工作流会推送 `ghcr.io/huangdale522-gif/workbuddy2api-hub:vX.Y.Z` 和 `:latest`。

`Publish Docker image` 也支持手动运行，只生成 `:edge` 和 `:sha-*` 测试镜像，不会覆盖稳定版 `:latest`。

## 回滚

- 应用回滚：重新部署上一个稳定版镜像标签。
- 发布回滚：保留旧标签与旧镜像，不删除历史 Release；必要时发布新的补丁版本。
- 数据目录 `accounts/`、`usage/`、`subbridge-data/` 与程序升级解耦，升级和回滚都不应删除这些目录。
