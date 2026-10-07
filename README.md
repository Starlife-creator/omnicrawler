<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/branding/lockup/omnicrawler-wordmark-dark.svg">
    <img alt="OmniCrawler" src="assets/branding/lockup/omnicrawler-wordmark.svg" width="900">
  </picture>
</p>

# OmniCrawler 0.15.0 — 桌面专业数据采集平台

> 可配置 · 可恢复 · 可扩展 · 可审计

> Copyright (C) 2026 Starlife-creator。本项目以 Apache-2.0 授权，详见 [LICENSE](LICENSE)。

> **商标与命名声明**：本项目（仓库名 `omnicrawler`、Python 包及 CLI 命令名 `omnicrawler`）由 Starlife-creator 独立开发与维护，为项目内部代号，**并非任何商业实体的产品或服务**。本项目与 [omnicrawl.dev](https://www.omnicrawl.dev/)（OmniCrawl 通用网页抓取服务）、学术基础设施 [OmniCrawl](https://github.com/jessejjohnson/OmniCrawl)（PETS'22）、[Oncrawl](https://www.oncrawl.com/)（法国 SEO 技术平台）等**无任何关联、授权、赞助或隶属关系**，亦不暗示与之存在任何联系。若上述名称的持有方主张权利，请通过仓库 Issues 联系作者协商。Apache-2.0 仅授予代码使用与分发权利，不授予任何商标或名称使用权。

OmniCrawler 是一个面向桌面与单机生产环境的模块化采集平台。从网站、API、动态页面和流式协议获取数据，下载附件，解析 PDF/OCR，完成结构化提取、质量检查、人工复核与多格式交付。

**v0.15.0** 在可验证的本地采集、文档抽取与可恢复管线之上，进一步把 GUI 首页改为“先描述需求、再补充必要信息”的任务入口：所有运行前必填项集中在第一页，自然语言输入会编译为可审阅的任务草案。通知、动画、导出进度与关闭流程也补齐了生命周期保护；发布一致性治理、本地可复用 E2E、无障碍/i18n、CLI、性能指标和 Windows 便携构建能力继续保持。

---

## 快速开始

### Windows 便携版（零依赖）

1. 解压当前构建生成的 `OmniCrawler-0.15.0-Windows-Portable-<Edition>.zip` 到可写目录（建议 `D:\OmniCrawler`）
2. 双击 `OmniCrawler.exe`；若没有窗口出现（例如 exe 被杀软首次扫描拦下，或文件缺失），再双击 `OmniCrawler-Launcher.bat` 查看中文报错
3. 在任务工作台确认自动草稿 → 试跑 3 页 → 正式运行

Standard 版：GUI + Chromium + 常规采集。Full 版：额外含 ChromeDriver + 双 OCR 引擎。

### 源码安装

源码版支持 Python 3.12+；CI 覆盖 Windows/Linux/macOS（3.12 为主测版本，3.13 做依赖导入 smoke）。推荐使用当前可用的最新受支持 Python 版本。

```powershell
py -3.12 -m venv .venv; .venv\Scripts\activate
pip install -e ".[full,dev]"
playwright install chromium
```

实验性异步 AI 字段提取可单独安装 `pip install -e ".[ai]"`；Office 文档能力使用 `[document]`。`[full]` 包含这些依赖。未启用相关能力时，核心采集不要求它们。

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

插件与模板完成后会先形成一份创作者整包签名目录，可直接私下分享，也可由作者自主选择投稿
市场；上传不是获得可分享状态的前置条件。身份归属认公钥指纹，不认用户名；重名只在正式
市场发布时分配稳定后缀。市场客户端会先验证目录签名并防回放，再验证创作者/维护者整包
双签。完整流程见 [插件作者指南](docs/AUTHOR_GUIDE.md) 与
[市场生态与分发协议](docs/MARKET_ECOSYSTEM.md)。

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
│  Config   │   Templates (registry)   │   Plugins        │
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
| 反检测隐身 | 按 `browser.stealth_level` 分级（off/low/medium/high，默认 low = stealth.min.js + webdriver 隐藏；medium/high 叠加分级指纹脚本）；UA 不参与随机化（诚实自报铁则不因隐身等级放宽） |
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

## 模板库（44 套）

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
| 简单模式 | 任务工作台 + 运行进度 + 结果浏览（新人首选） |
| 专业模式 | YAML 编辑器 + 高级规则 + 模板检测 + 定时任务 |
| 开发者模式 | 完整配置 + 插件 + 诊断工具 |

模式切换不丢失数据；保存时保留 GUI 尚未识别的扩展字段。

首页使用单一“网址或描述”入口生成安全草稿，并显示真实最近任务。任务工作台底部常驻试跑状态和运行入口；只有会影响采集结果的配置变化才要求重新试跑，输出与调度采用独立本地验证。

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

### 需求理解

```powershell
omnicrawler task "抓取 https://books.toscrape.com 的标题、价格，输出 CSV" [--fallback-url <url>]
```

把一句中文需求编译成**可审阅的任务设置**：字段清单、访问范围、输出格式，以及**当前做不到的部分**
（例如点击类交互需先用 `record-actions` 录制；排序 / 分组 / 聚合统计已有对等能力，见
「数据与导出」的 `transform`）。只做解析、不发起任何网络请求。

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
omnicrawler transform books.csv grouped.csv --map "价格 = parse_money(价格)" --group-by 分类 --agg "sum(价格_parsed):总价" --sort "总价:desc" --confirm
omnicrawler transform books.csv sorted.csv --sort "价格:desc" --dry-run
```

`transform` 对已落盘数据做「值级清洗 → 记录级排序/分组聚合」，不联网、默认不写文件
（`--confirm` 才落盘）。**先转数值再聚合**：CSV/JSONL 读入的单元格全是文本，
`sum`/`avg` 只认严格十进制字面量，所以金额列要先 `--map "价格 = parse_money(价格)"`
再对 `价格_parsed` 聚合；取不到数值时命令会报出跳过数并给一条可复制的补救命令
（一个函数都修不好时如实回报，不编建议）。

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
omnicrawler workspace {init|health|package|snapshot|rollback|import}
omnicrawler workspace package -c config.yaml --target workspace.zip --kind complete
omnicrawler workspace import --target workspace.zip --destination "新 工作区"
omnicrawler migrate -c config.yaml -o migrated.yaml [--force]
omnicrawler serve -c config.yaml [--host 127.0.0.1] [--port 8765]
```

`complete` 保留历史导出，适合备份和搬迁；`full` 保留既有精简行为，排除旧导出。导入会校验文件哈希和数据库，仅创建新目录。打开新目录中的 `config.yaml`；原配置副本和引用检查记录分别保存在 `config.before-relocation.yaml` 与 `workspace/relocation.json`。外部文件、未包含的附件及旧包缺少原位置的信息需要复核，原有凭据引用需在新环境中可用。

支持 JSON Schema 的 AI 端点可在 `ai.providers.<名称>` 中显式设置 `supports_json_schema: true`。资料分析与选择器候选会请求严格结构化响应；实验性字段提取由共享字段契约生成 schema，允许某个分块缺少字段，最终合并后仍检查必填项。未声明能力的端点保持原请求方式；所有路径继续执行本地类型、冲突及引用证据校验。该能力需要按具体端点和模型验证，不能从“OpenAI 兼容”推断支持。

PDF 工作台可填写 `2,4` 等 OCR 页码；页码作用于当前输入中的每份 PDF，留空处理全部待识别页。CLI 可运行 `omnicrawler pdf --config pdf.yaml ocr --page-number 2 --page-number 4`。未选页保留待识别状态，已识别页不重复 OCR。调用文档 IR 时可显式传入 `ocr_pages` 与本地 `ocr_backend`；识别文字保留页码、后端及置信度，缺少区域或阅读顺序证据时明确标注未验证，不自动初始化或下载 OCR 模型。

模板可以用 `omnicrawler templates verify-fixture <模板ID> --url <来源网址> --body-file sample.html --expected expected.json` 离线复验。真值为非空的原始提取记录 JSON 列表；这里只检查内置 HTML/JSON 处理器，后续质量校验、浏览器动作和导出需另行验收。失败在相同模板内容、来源网址及默认参数范围内暂离推荐，成功复验后恢复；自定义参数的失败不影响默认参数推荐。`omnicrawler templates verification-history <模板ID>` 保留历次成功、失败和版本摘要。证据过期只提示复验；已解析配置无效的模板也不进入默认推荐，仍可查看与修复。

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
.\build_windows.ps1 -Edition Standard -ReleaseOutputPath .\artifacts\release\0.15.0
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

完整文档索引见 **[docs/README.md](docs/README.md)**——架构、配置协议 v5、插件契约、打包发布、
ADR 与质量报告都从那里进入。`docs/archive/` 是历史归档，描述过去状态，不作为当前行为依据。

### 项目结构（v0.15.0）

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
├── templates/                   # 注册表发现的采集模板 + recipe
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
| coverage | 全源码 ≥75%，并执行分组门禁（长期目标 80%、核心 ≥85%） |
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

<!-- current-facts:start -->
当前内置模板：**41** 个稳定 ID；真源：`omnicrawler templates list`。配置协议：**v5**。
<!-- current-facts:end -->


记录字段变化通知可在 `updates.notifications` 中配置 `enabled: true`、`webhook_url` 和可选的 `webhook_token_ref: secret://...`。
首次同步建立基线；后续已观察到的新增/字段修改与待投递事件在同一数据库事务保存。未完成遍历不推断删除。
Webhook 接收地址必须满足当前任务 EgressBroker 的域名、私网、凭据作用域和预算设置；不继承抓取 Cookie。
正常完成后尝试投递；失败保留回执，不改变采集成功状态。GUI 任务工具的“变化通知”页可查看和补发所选事件，CLI 示例：

```bash
omnicrawler notifications --config task.yaml report
omnicrawler notifications --config task.yaml retry --event-id EVENT_ID --apply
```

更换接收地址或关闭通知会撤销当前任务旧目标的待投递事件；补发不会重新采集，也不会处理其他任务。
投递携带固定 `Idempotency-Key`；HTTP 回执丢失时仍可能重复，接收端需要去重。当前自动通知覆盖已观察到的新增与修改，删除保护继续由完整运行比较提供。

字段通知可配置 `updates.notifications.policy`：

```yaml
policy:
  confirmations: 2
  cooldown_seconds: 60
  fields:
    price:
      direction: decrease
      minimum_absolute_change: 5
      minimum_relative_change: 0.1
```

连续确认按不同采集运行计数，重复请求不增加确认次数；304 与未变化内容复用也参与确认。
每个实体独立保存规则状态。阈值与冷却以最后入队的值为基线，小变化可累积，冷却后在下一次实际观察时发送最新差异摘要。
同一字段的条件全部满足才触发，多个字段中任一个满足即可；未配置字段表示观察所有业务字段。
相对阈值遇到原值零时明确抑制；布尔值、缺项及非有限数不能当数字。被抑制的变化仍保留事实与原因，不能强行补发。
修改规则或接收目标后，规则状态从上一次实际观察初始化；这不重建采集事实基线，也不补发过去被抑制的事件。

业务事件通过显式字段映射接入同一通知队列，例如只关注截止时间提前或延期：

```yaml
policy:
  semantic_fields:
    closing_time: {kind: deadline}
    total: {kind: amount, unit_field: unit, currency_field: currency}
  event_types: [advanced, postponed, amount_changed]
```

`kind` 支持 `deadline/date/status/amount/price/budget`。不配置 `event_types` 时仅在通知详情附加事件解释；配置后，至少一条已映射字段的事件须匹配。原有字段阈值、确认与冷却条件继续生效。单位和币种只读取声明的字段，无法解释时不猜测转换；金额单位支持元、万元、亿元。同一时刻的不同时区表示视为未变。缺失字段、null、未知日期与状态不推断为撤回；观察时间不冒充生效时间，事件置信度仍为未知。不进行实体自动合并，也不将页面缺失解释为业务撤回。


CSV交付同时包含 `record_quality.csv`，Excel交付包含“质量与复核”工作表，均通过 `record_id` 与数据记录关联。
状态分为 `valid`（当前字段契约无需复核）、`review_required` 和 `unassessed`，字段错误、缺失、重复和异常保留明细。
JSONL继续保留原始结构化 `_quality` 证据；表格中的空值不等于已通过，业务字段不会被复核状态列覆盖。


游标分页诊断记录实际推进、发现、入队、拒绝及重复计数和停止原因。数字游标 `0` 可继续分页；重复续页会标记未完整遍历，避免把漏采报成完成。
诊断只保存游标SHA-256摘要，原始游标不进入步骤清单；配置深度/页数限制仍是采集边界。

记录删除通知可显式开启 `updates.notifications.include_removed: true`，需要持久化 `project.task_id` 和显式 `updates.identity_fields`。仅在完整、无抓取错误且范围一致的成功运行后确认删除；预算耗尽、部分续跑、未复访页面和 304 覆盖不明时保留复核原因。删除事实与投递事件原子落库，同值重新出现按新增处理。字段阈值策略作用于观测值变化，删除确认使用完整性检查。

PDF 文档解析可提供 `column_boundaries: [300]`（PDF 点，页面左上坐标系）明确先左列后右列读取，输出保留每行坐标和列号。边界穿过文字时拒绝模糊分割；跨列表格仍标记待复核。相邻页面出现同标题、同宽度表格时仅标记续表候选，不自动合并原始证据。任意复杂版式、旋转页面和扫描文档仍需逐页复核。


### PDF 版式校正

PDF 工作台的“版式预览与校正”可查看原页和分栏线，自动建议分栏或手动输入 PDF 点坐标，逐项确认跨页续表后导出独立 Markdown 和带来源区域的 JSON。自动分栏仍需核对；扫描页请先使用 OCR。校正结果不会覆盖原 PDF，也不会自动替换批量抽取结果。SDK 可使用 `auto_columns: true`，与 `column_boundaries` 互斥。

任务工具的“工作区与搬迁”支持检查声明的文件引用、选择替代文件或目录、预览复制或重新绑定，并生成保留任务身份的新配置。预览后文件或配置变化时须重新预览；原配置不会覆盖。

任务工具中的“检查本机浏览器兼容性”只访问独立本地样例，不执行任务动作、不加载账号会话，报告实际引擎可用性与回退条件。Selenium 的拦截订阅也受看门狗约束；持久化会话启动失败时保留原会话并提示修复。

登录会话列表展示 Cookie 到期数量，摘要不展示值且不修改快照。仅有已到期 Cookie 时不会同步给 HTTP，也不会恢复该站点的认证失败请求；未到期快照仍需用任务认证条件验证服务端状态。

AI 服务中心的连接检查读取模型列表，不生成内容。单次生成验收须明确模型单价和有限正数预算，仅发送固定文本，限制一次请求和16输出Token，可验证严格JSON Schema；账单金额仍以服务商为准。费用上限支持小数，兼容旧 max_cost，并保留其它预算与Provider参数。本地模型诊断须显式允许该端点，默认出口限制保留。

### 首次使用与离线导入

首页的“创建离线入门任务”会建立独立配置和示例数据，不需要联网、OCR 或模型。先小样本试跑，再正式运行，最后在结果页核对 CSV / Excel。进度工具按当前配置的真实试跑和运行证据给出下一步；规则修改后旧证据失效，人工交付核对不会自动标为通过。

文件来源可用 `source.local_files: [items.json]` 明确声明配置目录内的文件，也可用 `source.local_root` 指定输入目录；GUI 保存及 Worker 执行仍绑定该目录；种子只允许列出的文件 URI。不自动发现链接，不允许跨目录、符号链接、目录联接、浏览器渲染或超过 16 MiB 的文件。远程文件仍走原有 HTTP / HTTPS 网络策略。大型 PDF 使用 PDF 工作台。

### 离线回归与数值校验

启用 `OMNICRAWL_CAPSULE_ENABLED=true` 后，证据胶囊按响应内每条记录、每个字段保存。`timeline` 返回胶囊、响应和记录定位；`replay --record-index 2 --response-id 123` 或 `replay --capsule-id ID` 可离线选择历史提取动作。多条记录时不指定记录会返回 `ambiguous_record`；归档哈希缺失或不符会拒绝重放。HTML、JSON 和表格重放复用正式提取器；其他处理器明确报不支持。容量不足通过遗漏指标报告，重放输出是候选，不会覆盖人工复核值或发送通知。

PDF 批量 OCR 的词/文本块坐标、置信度及表格单元格结构会保存到页面状态，并通过 `pages.jsonl` 的 `ocr_structure` 导出。OCR 坐标采用左上角起点的图像像素，随结果保留识别 DPI；原生 PDF 区域继续使用 PDF 点坐标。表格结构保留表头和合并单元格跨度，Markdown/TXT 仅作为展示视图。重置 OCR 会清除旧结构，空文本或无效页面置信度不会标记为识别成功。

`omnicrawler regression -c config.yaml` 比较已捕获响应的提取器输出，包括记录顺序、字段值、类型和来源。同一请求及响应内容再次运行不会覆盖已有基线；不同内容保留独立样本（仍受样本容量限制）。这证明与捕获时一致，不代表人工确认正确，也不覆盖后续清洗、复核和最终交付。

旧的仅条数基线、没有样本、或当前不能重放的自定义提取/逐 URL 覆盖会报告未验证并返回非零；归档缺失或哈希不匹配也不会通过。请备份旧 `regression_fixtures` 后移到工作区外保留，再重新采集建立当前格式基线；不要把重新采集当作人工批准。已有基线不会自动刷新为新规则的结果。

非严格 JSON 的 number/float 校验继续接受数字字符串和规范千分位，科学计数法按实际数值比较；任意夹杂文字、非有限数及歧义逗号格式进入复核。money 校验复用金额解析，对万/亿倍率按元比较边界，不改写原字段、不执行汇率换算。业务单位文本应显式使用 money 或先执行有证据的归一化；strict_json 的原生类型要求保持。
# 浏览器数据就绪与虚拟列表

实验性 `AIGraphExtractor` 可用 `cache_entries` 开启进程内缓存（默认关闭，上限 1024 项和 8 MiB）。缓存键覆盖 HTML、字段定义、提示、模型、API 地址、输出预算、项目范围与 `rule_version`；修改规则版本会失效旧缓存。返回副本保留候选与冲突，缓存命中不重复计入模型费用，仍检查当前隐私授权。缓存不写磁盘。

OCR 富结果保留词、区域、表格单元格和跨度。PDF 字段的 `ocr_region` 保存匹配区域及坐标来源；只有唯一匹配且区域分数有效时才使用其识别分，没有定位依据时进入复核。识别分与规则评分均未校准为正确概率。Paddle 方向校正或展平后的坐标标为 `ocr_engine_pixels_top_left`、`original_mapping: unverified`；没有经验证的变换时不能直接叠加到原 PDF。组件 OCR 的结果可提供 `structure` 对象，含 `words`、`blocks`、`tables` 和 `metadata`；只有文本输出的组件会明确缺少字段区域依据。

Scrapy 桥接属于运行受信任 Python spider 的外部执行模式，需要显式设置 `source.execution_contract: trusted_external`。支持 `timeout_seconds`（默认 60，最多 300）、`max_output_bytes` 和取消请求；输出及日志受预算限制，退出成功后还必须验证 JSONL 对象，结果才会原子替换上一份文件。空结果默认失败，可用 `allow_empty: true` 明确接受。此模式不提供原生网络审计、字段质量或断点恢复；声明了原生出口限制的任务会被拒绝，避免误认为这些限制已对 spider 生效。

Crawl4AI 桥接使用本项目的受控 Playwright 渲染，再离线调用 Crawl4AI 的 Markdown 与 CSS/XPath 提取。未使用 Crawl4AI 自带的联网浏览器、自治探索、LLM 或隐匿模式；这些配置会明确报错。动态等待和虚拟列表使用上述原生浏览器配置，自适应探索使用主任务的 focused 调度。`process_html()` 可直接处理已有 HTML，无需联网；不再宣称未经基准验证的资源节省倍数。

重新提取遇到含人工修订的来源时，会保留该来源的已交付记录和编辑历史，将新的提取结果保存为 `reprocess_candidate` 阶段检查点，并把保留记录放入复核队列。不会按记录位置自动移植修订，也不会为这些候选发送变更通知。重提取摘要的 `manual_review_required` 表示需要审阅候选；导出继续使用保留的值。

动态页面可以通过 `browser.readiness` 声明 `selector`、`min_count`、`hidden_selector`（加载指示器）、`text_not`、`stable_ms` 和 `timeout_ms`。Playwright 还支持 `response_url` 模式匹配成功的 API 响应。数据就绪只代表可开始提取，不代表遍历完整。

虚拟列表使用 `browser.collection`：必须设置与 `extract.item_selector` 相同的 `item_selector`，以及 `container_selector`、`identity_attribute`。采集保存每个稳定标识对应的 HTML，保留滚动后被 DOM 移除的记录。用 `end_selector` 或 `expected_count` 声明结束条件；`max_steps`、`max_items`、`timeout_ms`、响应字节预算共同限制采集。未达到结束条件、标识内容变化或预算耗尽会标记为部分结果，并阻止发现阶段报告完整成功。空列表只有明确配置 `allow_empty: true` 且满足结束条件时才算完整。

同时设置 `end_selector` 和 `expected_count` 时，两项都必须满足且加载已结束。结束标记出现但记录不足，或采集数量超过声明值，均报告部分结果；不能用结束标记掩盖数量冲突，也不能仅因数量达到就提前结束声明了终点的遍历。

人工修订后的重提取结果可在“结果与复核 → 证据查看器 → 复核重提取候选”确认：显式选择对应候选、查看字段和来源证据差异、填写理由后接受或拒绝。接受替换整条数据（包括移除候选缺失字段），保留编辑历史和决定审计；拒绝保留当前值。陈旧候选与重复映射不能提交，不自动新增／删除记录或发送通知，已有导出文件需重新导出。
