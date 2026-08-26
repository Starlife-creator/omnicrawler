# OmniCrawler 0.11.1 — 桌面专业数据采集平台

> 可配置 · 可恢复 · 可扩展 · 可审计

> Copyright (C) 2026 Starlife-creator。本项目以 Apache-2.0 授权，详见 [LICENSE](LICENSE)。

> **商标与命名声明**：本项目（仓库名 `omnicrawler`、Python 包及 CLI 命令名 `omnicrawler`）由 Starlife-creator 独立开发与维护，为项目内部代号，**并非任何商业实体的产品或服务**。本项目与 [omnicrawl.dev](https://www.omnicrawl.dev/)（OmniCrawl 通用网页抓取服务）、学术基础设施 [OmniCrawl](https://github.com/jessejjohnson/OmniCrawl)（PETS'22）、[Oncrawl](https://www.oncrawl.com/)（法国 SEO 技术平台）等**无任何关联、授权、赞助或隶属关系**，亦不暗示与之存在任何联系。若上述名称的持有方主张权利，请通过仓库 Issues 联系作者协商。Apache-2.0 仅授予代码使用与分发权利，不授予任何商标或名称使用权。

OmniCrawler 是一个面向桌面与单机生产环境的模块化采集平台。从网站、API、动态页面和流式协议获取数据，下载附件，解析 PDF/OCR，完成结构化提取、质量检查、人工复核与多格式交付。

**v0.11.1** 在可验证的本地采集、文档抽取与可恢复管线之上，进一步把 GUI 首页改为“先描述需求、再补充必要信息”的任务入口：所有运行前必填项集中在第一页，自然语言输入会编译为可审阅的任务草案。通知、动画、导出进度与关闭流程也补齐了生命周期保护；发布一致性治理、本地可复用 E2E、无障碍/i18n、CLI、性能指标和 Windows 便携构建能力继续保持。

---

## 快速开始

### Windows 便携版（零依赖）

1. 解压当前构建生成的 `OmniCrawler-0.11.1-Windows-Portable-<Edition>.zip` 到可写目录（建议 `D:\OmniCrawler`）
2. 双击 `OmniCrawler-Launcher.bat`
3. 按五步向导完成配置 → 试跑 3 页 → 正式运行

Standard 版：GUI + Chromium + 常规采集。Full 版：额外含 ChromeDriver + 双 OCR 引擎。

### 源码安装

源码版支持 Python 3.12+；CI 覆盖 Windows/Linux/macOS（3.12 为主测版本，3.13 做依赖导入 smoke）。推荐使用当前可用的最新受支持 Python 版本。

```powershell
py -3.12 -m venv .venv; .venv\Scripts\activate
pip install -e ".[full,dev]"
playwright install chromium
```

### 双仓库布局（源码版必读）

插件市场采用 **git-as-registry** 模式，源码仓库与市场仓库**必须放在同一父目录下、且目录名保持默认**：

```
你的任意目录/
├── OmniCrawler/            # 本仓库（应用 + 引擎 + 插件生态）
└── OmniCrawler-market/     # 插件市场仓库（另库 clone）
```

```powershell
git clone https://github.com/<owner>/OmniCrawler
git clone https://github.com/<owner>/OmniCrawler-market
```

布局依赖说明（目录名与同级关系不可变，路径前缀无关）：

| 组件 | 引用方式 |
|---|---|
| `tools/market.py` | `../OmniCrawler-market` 作为默认 catalog 源 |
| `tools/sign_plugin.py` | `../OmniCrawler-market/tools/scan_plugin.py` 发布前扫描 |
| GUI 插件/模板市场 | 无 `catalog_url` 配置时回退到 `../OmniCrawler-market` 本地浏览 |
| `tests/unit/plugin/` | 市场相关测试引用同级市场仓库；未 clone 时自动跳过 |

只 clone 主仓库时应用完全可用（本地回退目录缺失即视为无市场）；要使用插件市场需同时 clone 两个仓库。私有签名路径默认写入 `~/.omnicrawler/keys/`（可用 `--private-out` / `--private-key` 覆盖）。

### 三分钟命令行

```powershell
# 模板发现与配置生成
omnicrawler templates inspect https://example.org
omnicrawler templates render generic/list-detail -o config.yaml --set seed_url=https://example.org

# 校验 → 计划 → 试跑 → 正式
omnicrawler validate -c config.yaml
omnicrawler plan -c config.yaml
omnicrawler sample -c config.yaml --pages 3
omnicrawler run -c config.yaml

# 中断恢复 + 重新处理
omnicrawler resume -c config.yaml
omnicrawler reprocess -c config.yaml --run-id <id>
```

---

## 架构总览

```
┌────────────────────────────────────────────────────────┐
│                  Desktop GUI (PySide6)                 │
│  Wizard(5-pages) │ Home │ Results │ Settings │ A11y    │
├────────────────────────────────────────────────────────┤
│             CLI (registry pattern, 47 cmds)             │
│  run │ resume │ validate │ doctor │ export │ ...        │
├────────────────────────────────────────────────────────┤
│               Pipeline (star orchestrator)             │
│  plan → discover → fetch → parse → filter              │
│       → quality → export → archive → cleanup           │
├────────────────────────────────────────────────────────┤
│  StateStore (SQLite WAL) │ EgressBroker (policy)       │
│  Config   │   Templates (78 sets)   │   Plugins        │
└────────────────────────────────────────────────────────┘
```

**核心设计决策**（详见 `docs/adr/`）：

| ADR | 决策 |
|-----|------|
| 配置模型 | AppConfig（运行时） + CrawlConfig（GUI 视图），不合并 |
| Pipeline | 星型编排器，非五层线性管道 |
| CLI | 字典注册表替代 if/elif 链 |
| 错误处理 | `errors.py` 层次化异常（11 直接子类 + 3 二级子类） + 单 URL 隔离 |

---

## 内置能力矩阵

### 数据源

| 类型 | 支持 |
|------|------|
| 静态 HTML | BFS/DFS/优先级/随机遍历、聚焦与增量采集 |
| 动态页面 | Playwright 浏览器池、隔离会话、动作链、XHR 捕获 |
| API | REST、GraphQL、表单、RSS/Atom、WebSocket/SSE |
| 文档 | PDF 文本提取、按页 OCR（Tesseract + PaddleOCR）、Office |

### 提取与质量

| 能力 | 说明 |
|------|------|
| 提取引擎 | CSS / XPath / JSONPath / JSON-LD / OpenGraph / Meta |
| 文档抽取 | doc_extractors 槽位抽取 + document_ir 统一中间表示（txt/html/docx/pptx/odt/epub/pdf，`document_type=auto`；HTML 正文容器抽取 main/article/#content 等，未命中回退全页） |
| 站点分类与模板推荐 | L1 硬止损 → L2 本地映射 → L3 受限嗅探 三层漏斗，输出命中来源 + 置信度，GUI/CLI 人工确认闸门 |
| AI 智能提取 | LLM 驱动字段提取（分块 HTML → 结构化输出），AI 服务中心可配置 |
| 智能模式 | AI 辅助字段推荐 + 自动选择器生成 |
| 质量检查 | 字段证据链、类型校验、正则、跨字段、异常检测 |
| 人工复核 | 字段复核台、证据查看器、来源清单 |

### 交付与存储

| 格式 | 支持 |
|------|------|
| 输出 | JSONL / CSV / Excel / Parquet / DuckDB / Markdown |
| 格式互转 | ConvertX：CSV/JSONL/Parquet/DuckDB/XLSX/文档族任意互转（Reader × Writer 矩阵，含 CLI 与 GUI 面板） |
| 存储 | 本地文件 / S3 兼容对象存储 / PostgreSQL / OpenSearch |
| 归档 | 原始响应归档 + 完整页面快照 + SHA-256 变更检测 + 审计记录 |

### 监控与反检测

| 能力 | 说明 |
|------|------|
| 变更监控 | URL 定时检查 + 内容哈希对比 + 变化 diff + 桌面通知 |
| 镜像注册表 | 多节点健康路由 + EWMA 评分 + 加权故障回退（经 SiteAliasRegistry 归一，EgressBroker 审计） |
| 反检测隐身 | 四级隐身等级（off/low/medium/high），可控指纹随机化 |
| 代理池 | 加权轮换 + 健康检查 + 按域绑定 |

### 场景与基因

| 能力 | 说明 |
|------|------|
| 场景管理 | 场景/槽位/选择器基因/候选验收闭环（SceneStore + 出厂场景幂等导入）；已验收候选文档级透视、导出已验收结果（JSON/CSV）与生成为任务字段（槽位→extract.fields YAML） |
| 证据胶囊 | 运行内提取动作时间线 + 限定重放（capsule_store + replay） |

### 安全与治理

| 能力 | 说明 |
|------|------|
| Egress Broker | HTTP/浏览器/附件/API 统一策略、预算、熔断 |
| 凭据管理 | `secret://` 引用 + 作用域限制 |
| 审计 | 网络访问边界报告 + AI 调用详情日志 |
| 脱敏 | 研究复现包（SHA-256 清单） |

---

## 模板库（78 套）

```powershell
omnicrawler templates list              # 按类别列出
omnicrawler templates recommend <url>   # 智能推荐
omnicrawler templates validate          # 校验完整性
```

覆盖场景：通用单页 · 列表详情 · 分页 · 无限滚动 · 表格 · 搜索 · 新闻 · 政务 · 电商 · 招聘 · 地产 · 金融 · 论文 · 社交媒体（Twitter/微博/知乎/小红书） · WordPress · Drupal · MediaWiki · Shopify · GitHub API · Crossref · OpenAlex

---

## 桌面 GUI 功能

### 三种模式

| 模式 | 说明 |
|------|------|
| 简单模式 | 五步向导 + 运行进度 + 结果浏览（新人首选） |
| 专业模式 | YAML 编辑器 + 高级规则 + 模板检测 + 定时任务 |
| 开发者模式 | 完整配置 + 插件 + 诊断工具 |

模式切换不丢失数据；保存时保留 GUI 尚未识别的扩展字段。

### 视觉与无障碍

- 3 套完整主题：明亮 / 暗黑 / 高对比度 + 色盲友好配色
- 语义化色彩令牌（VisualTokens）：全局 QSS 覆盖 40+ 控件
- 全局焦点可视化：所有可交互控件 2px 焦点框
- 5 个 Wizard 步骤页 ARIA 标签补齐
- 减帧模式（reduced-motion）支持
- 16 个基础 SVG Feather 风格矢量图标（icon_registry 当前共 23 个，含状态/监控图标）

### 国际化

- 567 条界面字符串已提取为 `.pot` 模板
- 英文翻译 `.po` 就绪（约 95% 覆盖）
- 新增语言：`python tools/generate_en_po.py` → 翻译 → `python tools/compile_i18n.py`

---

## CLI 命令参考（注册表模式）

### 任务执行

```powershell
omnicrawler run -c config.yaml [--max-pages N] [--progress]
omnicrawler resume -c config.yaml [--retry-failed]
omnicrawler validate -c config.yaml
omnicrawler doctor -c config.yaml
omnicrawler sample -c config.yaml --pages 3
```

### 状态与控制

```powershell
omnicrawler status -c config.yaml [--format json|text]
omnicrawler control -c config.yaml {pause|resume|stop}
omnicrawler recovery -c config.yaml {overview|continue|retry-failed|relogin|reprocess|rollback-config}
```

### 模板管理

```powershell
omnicrawler templates {list|recommend|render|validate|inspect|diff|merge} ...
omnicrawler templates export-pack <id...> --output pack.zip
omnicrawler templates import-pack pack.zip
```

### 数据与导出

```powershell
omnicrawler export -c config.yaml [--run-id <id>]
omnicrawler reprocess -c config.yaml --run-id <id>
omnicrawler compare-runs -c config.yaml <before> <after> -o diff.json
```

### 安全与审计

```powershell
omnicrawler security-report -c config.yaml
omnicrawler preflight -c config.yaml
omnicrawler research-package -c config.yaml -o research.zip [--include-raw]
```

### 备份与恢复

```powershell
omnicrawler backup create -c config.yaml -o backup.zip [--include-raw]
omnicrawler backup restore backup.zip --target ./restored/
```

### 性能与诊断

```powershell
omnicrawler benchmark -c config.yaml [--profile standard|high|all] [--output bench.json]
omnicrawler capabilities [--verify-imports] [--self-test] [--portable-paths]
omnicrawler regression -c config.yaml
```

### 组件与管理

```powershell
omnicrawler components list
omnicrawler plugins -c config.yaml
omnicrawler workspace {init|health|package|snapshot|rollback}
omnicrawler migrate -c config.yaml -o migrated.yaml [--force]
omnicrawler serve -c config.yaml [--host 127.0.0.1] [--port 8765]
```

---

## 开发者命令速查

日常开发常用命令与一键门禁，从这里开始。

### 环境准备（Windows）

```powershell
setup_windows.bat                    # 一键安装：venv + 依赖 + 运行时资产 + Playwright
install_windows.ps1 -Minimal          # 仅 venv + [html,gui] 依赖（无浏览器/运行时，最快）
install_windows.ps1                   # 完整：venv + [full,dev] + 浏览器 + OCR 资产
run_gui_windows.bat                   # 启动 GUI（自动 rebase 环境）
run_windows.bat                       # 启动 CLI
```

环境自动自愈：仓库内 `.runtime\python` + `.venv` 每次启动都会执行 `tools/rebase_venv.py`
自动对齐——项目目录搬移、版本 bump 后本地环境与源码收敛，无需手动重建。

```powershell
.venv\Scripts\python.exe tools\rebase_venv.py   # 手动触发对账（常规无需执行）
```

### 质量门禁（一键全套）

```powershell
# 单项
.venv\Scripts\python.exe tools\check_docs_consistency.py      # 版本/文档一致性
.venv\Scripts\python.exe tools\check_release_integrity.py     # 发布完整性
.venv\Scripts\python.exe tools\check_architecture.py          # 依赖架构
.venv\Scripts\python.exe tools\check_coding_standards.py src tools  # 编码规范
.venv\Scripts\python.exe tools\check_cli_docs.py               # CLI 文档一致性
.venv\Scripts\python.exe tools\check_network_boundaries.py     # 网络边界
.venv\Scripts\ruff.exe check src tests tools                   # lint
.venv\Scripts\python.exe -m mypy src/omnicrawler                 # 类型检查
.venv\Scripts\python.exe -m pytest -q                           # 测试
.venv\Scripts\python.exe -m coverage run -m pytest -q && .venv\Scripts\python.exe -m coverage json && .venv\Scripts\python.exe tools\check_coverage_gates.py coverage.json
```

### 版本发布

```powershell
# 自动 bump：更新 pyproject/__init__/文档 + CHANGELOG + 校验 + git commit/tag
.venv\Scripts\python.exe tools\bump_version.py <X.Y.Z>
# 只改版本不外动 git（如试运行）
.venv\Scripts\python.exe tools\bump_version.py <X.Y.Z> --no-git --report
```

发布时 `bump_version.py` 会自动运行环境版本对账；若本地 `.venv` 的 installed 版本
与源码新版本不符会终止发布。正式发布前应确保 `artifacts/` 的产物版本
== 当前版本（构建脚本会对 `omnicrawler.__version__` / `pyproject.toml` / installed
元数据做三重一致校验，不一致即失败）。

### 便携包构建

三平台构建总纲见 `docs/PORTABLE_PACKAGING.md`。各平台脚本：

```powershell
# Windows：完整构建（Full 版，含全部分发资产）
.\build_windows.ps1 -Edition Full
# Standard 版
.\build_windows.ps1 -Edition Standard
# 离线复用本地缓存（不访问网络，browser/runtime 走本地缓存）
.\build_windows.ps1 -Edition Full -Offline -BuilderPythonPath .venv\Scripts\python.exe
# 显式产物目录（默认写入 release/，建议写进 artifacts/）
.\build_windows.ps1 -Edition Standard -ReleaseOutputPath .\artifacts\release\0.11.1
```

```bash
# Linux：在 x86_64 / aarch64 上执行
./build_linux.sh                     # Standard
./build_linux.sh --edition Full      # Full（OCR 依赖系统 tesseract）
# macOS：只能在 macOS 上执行（PyInstaller 不支持交叉编译）
./build_macos.sh                     # Standard（ad-hoc 签名 .app → dmg）
./build_macos.sh --edition Full
```

### 提交前自检

```powershell
# pre-commit（已有配置 .pre-commit-config.yaml）
pre-commit run --all-files
```

## 开发者指南

### 项目结构（v0.11.1）

```
src/omnicrawler/
├── __init__.py, __main__.py     # 包入口（含旧路径兼容重定向）
├── cli/                         # CLI 入口 (_main.py 参数 + _handlers.py 分发)
├── core/                        # 配置、异常、迁移、站点分类器、编码、站点别名
├── pipeline/                    # 星型编排器 + 导出器
├── pipeline_ops/                # 任务 IR、计划、批处理
├── commands/                    # CLI 子命令处理器 (16 modules)
├── fetching/                    # HTTP/浏览器客户端、镜像路由、会话管理
├── extraction/                  # CSS/XPath/JSONPath/AI 提取引擎
├── doc_extractors/              # 文档/PDF 槽位抽取（document_type=auto 归一）
├── document_ir/                 # 统一文档中间表示（txt/html/docx/pptx/odt/epub）
├── quality/                     # 质量校验、证据链、观测与自动应用
├── review/                      # 人工复核台、证据查看器
├── security/                    # Egress Broker、沙箱、脱敏
├── runtime/                     # 状态存储、仓库、锁定
├── services/                    # 应用服务编排、统一进度协议、场景基因
├── state/                       # SQLite WAL schema + StateStore/SceneStore/CapsuleStore
├── sdk/                         # 公共 API（稳定性标记）
├── templates/                   # 78 套采集模板 + recipe
├── pdfx/                        # PDF 解析/OCR/抽取子系统
├── convertx/                    # 任意格式互转（CSV/JSONL/Parquet/DuckDB/文档族）
├── sources/                     # 数据源适配器、镜像注册表
├── plugins/                     # 插件系统
├── scenes/                      # 出厂场景定义（annual_report.yaml）
├── data/                        # 内置数据（站点分类默认域名映射）
├── apps/                        # PDF 处理独立 CLI（pdf-process / pdf-extract）
├── visual_selector/             # 可视化选择器（WebSocket 服务 + 字段转换）
├── gui/                         # PySide6 桌面界面
│   ├── views/                   # 首页/向导/结果/设置/PDF工作台/证据/变更监控/反检测/场景/格式互转/插件市场/开发者检查器
│   ├── widgets/                 # Toast, LogConsole, StatusIndicator, ...
│   └── delegates/               # Menu, Toolbar, Theme, Config, ...
├── scheduling/                  # 变更检测引擎
└── export/                      # Markdown 导出器
```

`locale/`（.pot + 翻译文件）位于仓库根目录，随 PyInstaller 便携包分发；源码形态由 i18n 沿父链自动发现。

### 质量门禁

| 工具 | 状态 |
|------|------|
| ruff (lint + format) | 0 violations |
| mypy (gui/core strict) | 通过 |
| pytest | 以 CI 全量结果为准；本地基线见 `docs/TEST_REPORT.md` |
| coverage | 全源码 ≥66%，并执行分组门禁（下一目标：总体 70%、长期目标 80%、核心 ≥85%） |
| pre-commit hooks | 已配置 |

### 贡献流程

1. 阅读 `CONTRIBUTING.md` 和 `docs/adr/`
2. 创建 feature 分支，PR ≤ 400 行
3. 通过 `pre-commit run --all-files`
4. 新增测试覆盖变更路径
5. 更新 `CHANGELOG.md` 和本文档中的功能列表

### 添加新 CLI 命令

```python
# src/omnicrawler/cli/_main.py                # 参数定义
my_cmd = sub.add_parser("my-command", help="命令描述")
my_cmd.add_argument("--flag")

# src/omnicrawler/cli/_handlers.py             # 执行逻辑
from omnicrawler.cli._handlers import _register

@_register("my-command")
def _handle_my_command(args: argparse.Namespace) -> None:
    print(f"Hello {args.flag}")
```

### 添加新语言翻译

```bash
python tools/extract_i18n.py                    # 更新 .pot
# 复制 locale/en_US/ → locale/xx_XX/
# 翻译 .po 文件中的 msgstr
python tools/compile_i18n.py xx_XX              # 编译 .mo
```

---

## 视觉回归测试

```bash
OMNI_BASELINE=1 pytest tests/gui/visual/ -v     # 生成基线
pytest tests/gui/visual/ -v                      # 像素级对比
```

---

*OmniCrawler 是一个合规的数据采集工具。"支持各种网站"指公开且允许自动访问的内容，以及用户有权访问的系统。项目不会绕过验证码、付费墙或站点安全策略。*

---

## AI 使用声明

本项目在开发过程中使用了 AI 编程助手作为辅助工具。所有 AI 生成的代码均经过人工审查、测试和质量门禁（ruff / mypy / pytest / coverage）验证后方可合入。最终设计决策和代码质量由项目维护者负责。

---

## 致谢

本项目在架构设计和工程实现上借鉴了以下开源项目的思路与模式，特此致谢：

| 项目 | 许可证 | 借鉴内容 |
|------|--------|----------|
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | Apache-2.0 | AI 驱动 HTML 分块→LLM→结构化提取模式 |
| [Scrapy](https://github.com/scrapy/scrapy) | BSD-3-Clause | Engine/Scheduler/Downloader/Middleware/Pipeline 分层架构 |
| [Crawlee Python](https://github.com/apify/crawlee-python) | Apache-2.0 | 请求管理、会话、资源感知并发与生命周期钩子 |
| [MarkItDown](https://github.com/microsoft/markitdown) | MIT | 网页/文档→结构化 Markdown 转换设计 |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | Apache-2.0 | URL 监控、内容哈希对比与变化通知机制 |
| [Playwright Python](https://github.com/microsoft/playwright-python) | Apache-2.0 | BrowserContext 隔离与网络监听 |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Apache-2.0 | 文档预处理、布局与 OCR 流水线 |
| [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) | Apache-2.0 | 多语言 OCR 引擎 |

完整第三方声明见 [`NOTICE`](NOTICE) 文件和 [`docs/RESEARCH_AND_FUSION.md`](docs/RESEARCH_AND_FUSION.md)。
