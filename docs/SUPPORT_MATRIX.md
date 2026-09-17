# OmniCrawler 0.13.0 支持矩阵

> 适用版本：0.13.0 · 配置协议：v5 · 维护状态：现行

本文是当前版本面向用户和维护者的运行时支持说明。历史发行说明与兼容性文档保留其原始版本，不以本文为准。

## Python 与平台

- 源码安装支持 Python 3.12+。
- 持续集成覆盖 Windows、Linux 与 macOS（3.12 为主测版本，3.13 做依赖导入 smoke）。
- 便携版构建支持 Windows（ZIP）、Linux（tar.gz）与 macOS（dmg）；macOS 产物为 ad-hoc 签名（无 Developer ID，首次打开需右键 → 打开）。

## 能力分级

| 分级 | 能力 | 验证方式 |
|---|---|---|
| 基础 | YAML、静态 HTML、SQLite WAL、JSONL/CSV、模板、恢复 | 单元/集成测试与质量工作流 |
| Standard | PyQt6、Playwright Chromium、PDF 文本、Excel、异步 HTTP、流协议 | GUI/浏览器工作流与便携版冒烟 |
| Full | Selenium、Tesseract、PaddleOCR 和离线模型 | Windows Full 依赖矩阵与便携版构建验证 |
| 可选 | Redis、Scrapy、S3、DuckDB、Parquet、PostgreSQL、OpenSearch | 按依赖安装后通过 `omnicrawler capabilities` 检查 |
| 显式例外 | 私网、代理、未拦截 Selenium、外部插件与外部 AI | 用户配置确认、出口审计与安全报告 |

### clone 之后先跑到哪一层

**依赖是可选的**（base 只有 `PyYAML` + `defusedxml`），所以不必一次装全：

| 层 | 一条命令 | 立刻能做 |
|---|---|---|
| 最小 | `pip install -e ".[dev]"` | 全部单元测试；走查演示站点的列表 / 详情 / 翻页 |
| 推荐 | 一键脚本（见 [安装、运行与平台矩阵](INSTALLATION.md)）| 完整 8 步人工走查（含浏览器与 tesseract） |
| 按需 | `pip install -e ".[dev,pdf,gui,html]"` | 在最小层之上补 PDF 附件与 GUI 走查 |

补齐不必靠猜：`python tools/walkthrough_env.py` 会打印**当前机器的能力清单**
（哪项缺、一条补齐命令）。缺可选依赖只**降级**对应步骤（并说明怎么补），
不会让其余步骤不可用 —— 这条原则见 [人工走查清单](MANUAL_WALKTHROUGH.md)。

## 质量与性能

- 全源码覆盖率门禁为 >= 73%；下一阶段目标为 >= 80%，安全、状态、管线、PDF/OCR、桌面核心另有分组门禁。
- 性能优化必须保留或提高基准任务的吞吐、延迟、内存和恢复效率；无法证明的回归不应合入。
- `omnicrawler capabilities --mode quick` 只检查核心运行条件；`--mode task --require browser` 等只导入当前任务所需组件；`--mode deep` 或兼容选项 `--verify-imports` 才导入全部已安装模块。三者均不把未安装的可选能力误报为可用。

## 发布包

- Standard：GUI、Chromium 与常规采集能力。
- Full：在 Standard 基础上增加 ChromeDriver、Tesseract、PaddleOCR 及离线模型运行时。
- 每个 Windows 构建会生成 `RELEASE-INFO.json`，记录 Edition、版本、文件规模、必需组件与运行时清单是否存在。
- 构建流程还会生成 provenance 记录；缺少不可变 commit、tag、托管 CI 或已验证签名时必须标记为 `internal_candidate`，不得作为正式公开版本分发。
- 本地 E2E 仅访问临时本机服务；核心链路和 Chromium 扩展均可复用，E2E 支撑代码覆盖率门禁为 >= 95%。
- 便携版的最终可用能力仍以包内运行时完整性校验和 `omnicrawler capabilities` 输出为准。
