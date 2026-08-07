# OmniCrawler 2.6.0 发布报告

> 发布日期：2026-07-30；配置协议：v5；公共 API：兼容。

## 发布结论

2.6.0 是一次以可验证交付为中心的版本升级：保留既有采集、PDF/OCR、桌面 GUI 和可恢复任务能力，同时把本地可复用 E2E 与版本一致性纳入质量链。它不引入云端依赖，也不改变已有工作区、任务配置或输出格式。

## 已交付优化

- E2E 测试只访问临时本地 HTTP 服务，覆盖 HTML 抓取、PDF 下载与字段提取、结构化交付、幂等重跑、CLI 校验/计划编译。
- 可选 Chromium 场景覆盖动态渲染、XHR JSON 捕获和浏览器池复用；不启用时核心 E2E 不要求浏览器。
- E2E 报告自动读取项目元数据版本，明确报告 100% 场景通过率和 E2E 支撑代码覆盖率，后者门禁为 95%，不以此替代全源码覆盖率。
- 发布一致性检查验证 `pyproject.toml`、运行时 `__version__`、当前 README、用户指南、支持矩阵、更新日志和当前 Agent 指引的一致性。

## 验证基线

- 常规全项目回归：416 passed、3 skipped、1 warning（51.64 秒）；全源码覆盖率 66.58%，满足当前 >= 66% 门禁。
- 启用本地 Chromium 的全项目回归：413 passed、2 skipped、1 warning（53.50 秒）。
- 本地 Chromium E2E：4 passed（8.70 秒）；E2E 支撑代码覆盖率 98.95%，满足 95% 门禁。
- 具体可复跑命令和最新机器生成结论见 `../../e2e/README.md` 与 `../../E2E_TEST_REPORT.md`。

## 离线重建交付物

本次重建没有下载依赖、浏览器或模型；仅使用已验证的本地 Python 环境与运行时缓存。旧的 2.3.1 产物未被覆盖。

| 交付物 | 位置 | 验证结果 |
|---|---|---|
| Standard 完整目录 | `../../artifacts/build/2.6.0-standard-r1/release/OmniCrawler/` | 5,776 个运行时文件完整，便携冒烟通过 |
| Full 完整目录 | `../../artifacts/build/2.6.0-full-r1/release/OmniCrawler/` | 12,262 个运行时文件完整，便携冒烟通过 |
| Standard ZIP | `../../artifacts/release/2.6.0/OmniCrawler-2.6.0-Windows-Portable-Standard.zip` | 深度完整性检查通过 |
| Full ZIP | `../../artifacts/release/2.6.0/OmniCrawler-2.6.0-Windows-Portable-Full.zip` | 深度完整性检查通过 |
| 源码 ZIP 与 wheel | `../../artifacts/python/2.6.0/` | 从干净源码构建并通过完整性检查 |

两份 ZIP 的 SHA-256 以 `../../artifacts/release/2.6.0/SHA256SUMS-2.6.0.txt` 为唯一权威来源。该清单不放入 ZIP 本体，避免发布报告与其所属 ZIP 的哈希形成自指循环。

最终验证包括 416 passed、3 skipped、1 个已知弃用警告的全项目 pytest，Ruff、Mypy（225 个源文件）、文档一致性、架构依赖与发布物深度完整性检查。网络边界扫描没有发现新增未分类直连；4 条历史兼容路径仍处于明确的迁移观察模式。

## 升级与回滚

- 从 2.0+ 升级：继续使用既有迁移流程，配置协议保持 v5。
- 回滚：先退出 GUI/Worker 并备份工作区；旧版本不识别的字段按既有未知字段策略处理。
- Windows 包名由构建脚本依据元数据生成；请使用 `OmniCrawler-2.6.0-Windows-Portable-<Edition>.zip`，不要依赖旧版本固定文件名。
