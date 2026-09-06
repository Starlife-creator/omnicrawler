# OmniCrawler 文档导航

当前版本 **0.12.0**，配置协议 **v5**，覆盖率门禁 **≥66%**。

本页是仓库内文档的唯一索引入口。未出现在本页的 `docs/*.md` 视为孤儿页，会被
`tools/check_docs_consistency.py` 拦截。新增或删除顶层文档时必须同步更新第 2 节。

## 1. 从这里开始

| 你是谁 | 先看这几篇 |
|---|---|
| 终端用户 | [低门槛使用指南](UX_GUIDE.md)、[FAQ](FAQ.md) |
| 运维 / 部署 | [安装、运行与平台矩阵](INSTALLATION.md)、[生产部署指南](PRODUCTION_GUIDE.md)、[生产运行、恢复与质量验收](OPERATIONS.md) |
| 桌面 GUI 用户 | [桌面运行、工作区与组件](DESKTOP_RUNTIME_1.4.md)、[桌面视觉与交互规范](GUI_DESIGN_2.1.md) |
| 插件作者 | [插件契约](PLUGIN_CONTRACT.md)、[插件作者指南](AUTHOR_GUIDE.md)、[SDK 使用指南](SDK_USAGE.md) |
| 代码贡献者 | 根目录 [CONTRIBUTING.md](../CONTRIBUTING.md)、[编码规范](CODING_STANDARDS.md)、[架构](ARCHITECTURE.md) |

## 2. 现行文档

### 架构与设计

- [架构](ARCHITECTURE.md)：模块边界、执行模型与兼容性原则。
- [配置参考](CONFIG_REFERENCE.md)：YAML 配置协议 v5 字段全集。
- [编码规范](CODING_STANDARDS.md)
- [能力成熟度矩阵](CAPABILITY_MATURITY.md)
- [支持矩阵](SUPPORT_MATRIX.md)
- [兼容与回滚](COMPATIBILITY_0.12.0.md)

### 安装、运行与运维

- [安装、运行与平台矩阵](INSTALLATION.md)
- [生产部署指南](PRODUCTION_GUIDE.md)
- [生产运行、恢复与质量验收](OPERATIONS.md)
- [低门槛使用指南](UX_GUIDE.md)
- [FAQ](FAQ.md)

### 桌面与交互

- [桌面运行、工作区与组件](DESKTOP_RUNTIME_1.4.md)
- [桌面视觉与交互规范](GUI_DESIGN_2.1.md)
- [任务创建体验重构 PRD](WIZARD_UX_PRD.md)

### 插件、模板与市场生态

- [插件契约](PLUGIN_CONTRACT.md)：插件 API v1。
- [插件作者指南](AUTHOR_GUIDE.md)：契约 2 插件从创建、私下分享到可选市场投稿的完整流程。
- [SDK 使用指南](SDK_USAGE.md)
- [2.0 SDK 与插件兼容政策](SDK_PLUGIN_2.0.md)
- [市场生态与分发协议](MARKET_ECOSYSTEM.md)
- [插件市场人工审核清单](PLUGIN_REVIEW_CHECKLIST.md)：`reviewed` 质量档的人工审核流程。
- [新增站点：优先模板，必要时插件](ADDING_A_SITE.md)
- [生态观察清单](ECOSYSTEM_OBSERVATION.md)
- [分布式与外部服务扩展](DISTRIBUTED.md)

### 安全、打包与发布

- [安全与合规](SECURITY_AND_COMPLIANCE.md)
- [便携版构建（Windows / Linux / macOS）](PORTABLE_PACKAGING.md)
- [Windows 便携版构建](WINDOWS_PACKAGING.md)
- [发布流水线故障排查手册](RELEASE_PIPELINE_TROUBLESHOOTING.md)：案例基于 v0.9.1 全轮次复盘，排查方法长期有效。

### 研究与规划

- [成熟项目研究、融合映射与许可边界](RESEARCH_AND_FUSION.md)
- [优化实施方案（2026-09）](OPTIMIZATION_PLAN_2026-09.md)

### 质量报告

- [测试报告](TEST_REPORT.md)：全项目回归基线。
- [E2E 测试结果](E2E_TEST_REPORT.md)：本地 E2E 结论。

### 架构决策记录（ADR）

- [ADR-000 模板](adr/0000-template.md)
- [ADR-001 插件市场 Catalog 托管与双仓就绪设计](ADR-001-plugin-catalog.md)：状态已取代，保留作决策追溯。
- [ADR-0001 DuckDB 列白名单](adr/0001-duckdb-column-whitelist.md)
- [ADR-005 配置字段审计](adr/ADR-005-config-field-audit.md)

## 3. 自动生成产物

以下位置由工具链写入，**不要手写编辑**：

- [docs/releases/](releases/README.md)：按版本存放的发布报告。`tools/bump_version.py` 在版本
  bump 时自动重命名旧报告并生成占位；`tools/check_docs_consistency.py` 会校验当前版本报告存在。

## 4. 归档（内容已冻结，不作为当前行为依据）

- [docs/archive/](archive/README.md)：历史审计、评估报告与旧版兼容文档。**描述的是过去的
  状态，不代表当前行为**，仅在追溯决策理由时查阅。

  注意：其中 `omnicrawler-evaluation-report/_shared/` 下存放随仓再分发的第三方字体与 JS 资产，
  根目录 `THIRD_PARTY_NOTICES.md` 依赖这些文件完成许可声明。清理归档时**不得删除该目录**。

## 维护约定

`tools/check_docs_consistency.py` 强制以下四条，防止文档再次腐化：

1. 本页必须被根目录 `README.md` 与 `CONTRIBUTING.md` 引用（防导航页变孤儿）。
2. `docs/` 下每个顶层 Markdown 都必须被本页第 2 节收录（防文档变孤儿）。
3. `docs/archive/` 与 `docs/releases/` 必须各有一份门页 README。
4. 每篇现行文档首部须带版本元信息（适用版本 / 配置协议 / 维护状态）。
