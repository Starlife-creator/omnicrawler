# OmniCrawler 2.7.0 — 桌面专业数据采集平台

> 可配置 · 可恢复 · 可扩展 · 可审计

OmniCrawler 是一个面向桌面与单机生产环境的模块化采集平台。从网站、API、动态页面和流式协议获取数据，下载附件，解析 PDF/OCR，完成结构化提取、质量检查、人工复核与多格式交付。

**v2.7.0** 在可验证的本地采集、文档抽取与可恢复管线之上，进一步把 GUI 首页改为“先描述需求、再补充必要信息”的任务入口：所有运行前必填项集中在第一页，自然语言输入会编译为可审阅的任务草案。通知、动画、导出进度与关闭流程也补齐了生命周期保护；发布一致性治理、本地可复用 E2E、无障碍/i18n、CLI、性能指标和 Windows 便携构建能力继续保持。

---

## 快速开始

### Windows 便携版（零依赖）

1. 解压当前构建生成的 `OmniCrawler-2.7.0-Windows-Portable-<Edition>.zip` 到可写目录（建议 `D:\OmniCrawler`）
2. 双击 `OmniCrawler-Launcher.bat`
3. 按五步向导完成配置 → 试跑 3 页 → 正式运行

Standard 版：GUI + Chromium + 常规采集。Full 版：额外含 ChromeDriver + 双 OCR 引擎。

### 源码安装

源码版支持 Python 3.10+；CI 覆盖 Windows/Linux 的 3.10、3.12 和 3.13。推荐使用当前可用的最新受支持 Python 版本。

```powershell
py -3.10 -m venv .venv; .venv\Scripts\activate
pip install -e ".[full,dev]"
playwright install chromium
```

### 三分钟命令行

```powershell
# 模板发现与配置生成
omnicrawl templates inspect https://example.org
omnicrawl templates render generic/list-detail -o config.yaml --set seed_url=https://example.org

# 校验 → 计划 → 试跑 → 正式
omnicrawl validate -c config.yaml
omnicrawl plan -c config.yaml
omnicrawl sample -c config.yaml --pages 3
omnicrawl run -c config.yaml

# 中断恢复 + 重新处理
omnicrawl resume -c config.yaml
omnicrawl reprocess -c config.yaml --run-id <id>
```

---

## 架构总览

```
┌─────────────────────────────────────────────────────┐
│                   Desktop GUI (PyQt6)                │
│  Wizard(5-pages) │ Home │ Results │ Settings │ A11y  │
├─────────────────────────────────────────────────────┤
│              CLI (注册表模式, ~20 子命令)              │
│  run │ resume │ validate │ doctor │ export │ ...     │
├─────────────────────────────────────────────────────┤
│                Pipeline (星型编排器)                  │
│  plan → discover → fetch → parse → filter           │
│       → quality → export → archive → cleanup         │
├─────────────────────────────────────────────────────┤
│   StateStore (SQLite WAL) │ EgressBroker (策略)      │
│   Config   │   Templates (67套)   │   Plugins        │
└─────────────────────────────────────────────────────┘
```

**核心设计决策**（详见 `docs/adr/`）：

| ADR | 决策 |
|-----|------|
| 配置模型 | AppConfig（运行时） + CrawlConfig（GUI 视图），不合并 |
| Pipeline | 星型编排器，非五层线性管道 |
| CLI | 字典注册表替代 if/elif 链 |
| 错误处理 | `errors.py` 11 子类层次结构 + 单 URL 隔离 |

---

## 内置能力矩阵

### 数据源

| 类型 | 支持 |
|------|------|
| 静态 HTML | BFS/DFS/优先级/随机遍历、聚焦与增量采集 |
| 动态页面 | Playwright 浏览器池、隔离会话、动作链、XHR 捕获 |
| API | REST、GraphQL、表��、RSS/Atom、WebSocket/SSE |
| 文档 | PDF 文本提取、按页 OCR（Tesseract + PaddleOCR）、Office |

### 提取与质量

| 能力 | 说明 |
|------|------|
| 提取引擎 | CSS / XPath / JSONPath / JSON-LD / OpenGraph / Meta |
| 智能模式 | AI 辅助字段推荐 + 自动选择器生成 |
| 质量检查 | 字段证据链、类型校验、正则、跨字段、异常检测 |
| 人工复核 | 字段复核台（开发中）、证据页、来源清单 |

### 交付与存储

| 格式 | 支持 |
|------|------|
| 输出 | JSONL / CSV / Excel / Parquet / DuckDB |
| 存储 | 本地文件 / S3 兼容对象存储 / PostgreSQL / OpenSearch |
| 归档 | 原始响应归档 + SHA-256 变更检测 + 审计记录 |

### 安全与治理

| 能力 | 说明 |
|------|------|
| Egress Broker | HTTP/浏览器/附件/API 统一策略、预算、熔断 |
| 凭据管理 | `secret://` 引用 + 作用域限制 |
| 审计 | 网络访问边界报告 + AI 调用详情日志 |
| 脱敏 | 研究复现包（SHA-256 清单） |

---

## 模板库（67 套）

```powershell
omnicrawl templates list              # 按类别列出
omnicrawl templates recommend <url>   # 智能推荐
omnicrawl templates validate          # 校验完整性
```

覆盖场景：通用单页 · 列表详情 · 分页 · 无限滚动 · 表格 · 搜索 · 新闻 · 政务 · 电商 · 招聘 · 地产 · 金融 · 论文 · WordPress · Drupal · MediaWiki · Shopify · GitHub API · Crossref · OpenAlex

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
- 16 个 SVG Feather 风格矢量图标

### 国际化

- 556 条界面字符串已提取为 `.pot` 模板
- 英文翻译 `.po` 就绪（部分覆盖）
- 新增语言：`python tools/generate_xx_po.py` → 翻译 → `python tools/compile_i18n.py`

---

## CLI 命令参考（注册表模式）

### 任务执行

```powershell
omnicrawl run -c config.yaml [--max-pages N] [--progress]
omnicrawl resume -c config.yaml [--retry-failed]
omnicrawl validate -c config.yaml
omnicrawl doctor -c config.yaml
omnicrawl sample -c config.yaml --pages 3
```

### 状态与控制

```powershell
omnicrawl status -c config.yaml [--format json|text]
omnicrawl control -c config.yaml {pause|resume|stop}
omnicrawl recovery -c config.yaml {overview|continue|retry-failed|relogin}
```

### 模板管理

```powershell
omnicrawl templates {list|recommend|render|validate|inspect|diff|merge} ...
omnicrawl templates export-pack <id...> --output pack.zip
omnicrawl templates import-pack pack.zip
```

### 数据与导出

```powershell
omnicrawl export -c config.yaml [--run-id <id>]
omnicrawl reprocess -c config.yaml --run-id <id>
omnicrawl compare-runs -c config.yaml <before> <after> -o diff.json
```

### 安全与审计

```powershell
omnicrawl security-report -c config.yaml
omnicrawl preflight -c config.yaml
omnicrawl research-package -c config.yaml -o research.zip [--include-raw]
```

### 备份与恢复

```powershell
omnicrawl backup create -c config.yaml -o backup.zip [--include-raw]
omnicrawl backup restore backup.zip --target ./restored/
```

### 性能与诊断

```powershell
omnicrawl benchmark -c config.yaml [--profile standard|high|all] [--output bench.json]
omnicrawl capabilities [--verify-imports] [--self-test] [--portable-paths]
omnicrawl regression -c config.yaml
```

### 组件与管理

```powershell
omnicrawl components list
omnicrawl plugins -c config.yaml
omnicrawl workspace {init|open|doctor|cleanup}
omnicrawl migrate -c config.yaml -o migrated.yaml [--force]
omnicrawl serve -c config.yaml [--host 127.0.0.1] [--port 8765]
```

---

## 开发者指南

### 项目结构（v2.7.0）

```
src/omnicrawl/
├── cli.py, cli_commands.py    # CLI 入口 + 注册表
├── config.py                   # AppConfig（运行时真相源）
├── errors.py                   # 11 子类异常层次
├── runtime_paths.py            # 跨平台路径（已从 gui/ 提升）
��── pipeline/                   # 星型编排器 (core/parallel.py)
├── commands/                   # CLI 子命令处理器 (13 modules)
├── state/                      # SQLite WAL 状态存储
├── gui/                        # PyQt6 桌面界面
│   ├── design_system.py        # VisualTokens + QSS + 主题管理
│   ├── icon_registry.py        # SVG 图标管线 (16 icons)
│   ├── motion_signal.py        # 减帧信号总线
│   ├── wizard/                 # 5 步配置向导
│   ├── widgets/                # Toast, LogConsole, StatusIndicator, ...
│   └── delegates/              # Menu, Toolbar, Theme, Config, ...
├── sdk/                        # 公共 API (_all__ + 稳定性标记)
├── tools/                      # extract_i18n, generate_xx_po, compile_i18n
└── locale/                     # .pot + en_US/LC_MESSAGES/
```

### 质量门禁

| 工具 | 状态 |
|------|------|
| ruff (lint + format) | 0 violations |
| mypy (gui/core strict) | 通过 |
| pytest | 以 CI 全量结果为准；本地基线见 `docs/TEST_REPORT.md` |
| coverage | 全源码 ≥66%，并执行分组门禁（下一目标：总体 70%、长期目标 80%、核心 ≥85%） |
| pre-commit hooks | 已配置 |

### 优化演进（2.2.0 → 2.7.0）

| 指标 | 2.2.0 | 2.7.0 |
|------|-------|-------|
| ruff violations | 0 | 0 |
| 测试数量 | 229 passed | 持续增长；以 CI 收集与执行结果为准 |
| 类型注解风格 | 混合（Optional/Dict/List） | 统一 Python 3.10+（`str \| None`/`dict`/`list`） |
| GUI main.py 行数 | 2730 | 1666（-39%） |
| 覆盖率门禁 | 70% | 66% 全源码 + 分组门禁；E2E 支撑代码 >=95% |
| mypy 覆盖范围 | 排除 GUI | 包含 GUI（Phase 1） |
| SDK docstring | 部分 | 完整 |

### 后续建议

- 在完整依赖环境下运行全量测试 + 覆盖率统计，验证总体 66% 与分组门禁；本地 E2E 另有 95% 支撑代码门禁
- 逐步收紧 mypy GUI overrides（Phase 2: 开启 disallow_untyped_defs）
- 覆盖率阶梯继续提升：66% → 70% → 75% → 80%（核心 >=85%）
- 为 BenchmarkRunner 添加 CLI 集成（`omnicrawl benchmark`）

### 贡献流程

1. 阅读 `CONTRIBUTING.md` 和 `docs/adr/`
2. 创建 feature 分支，PR ≤ 400 行
3. 通过 `pre-commit run --all-files`
4. 新增测试覆盖变更路径
5. 更新 `CHANGELOG.md` 和本文档中的功能列表

### 添加新 CLI 命令

```python
# src/omnicrawl/cli_commands.py
from omnicrawl.cli_commands import _reg

_reg("my-command",
     lambda p: p.add_argument("--flag"),
     lambda a: print(f"Hello {a.flag}"))
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
