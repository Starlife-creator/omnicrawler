# OmniCrawler 0.8.0 — 全能采集平台

> 从全类型网站采集、PDF 解析/OCR 到结构化导出和人工复核的模块化工作台。
> 支持可视化点选、零配置智能分析、AI 驱动提取、反检测执行。

---

## 目录

1. [项目概述](#1-项目概述)
2. [核心能力矩阵](#2-核心能力矩阵)
3. [安装指南](#3-安装指南)
4. [5 分钟快速开始](#4-5-分钟快速开始)
5. [CLI 命令完整参考](#5-cli-命令完整参考)
6. [GUI 桌面使用指南](#6-gui-桌面使用指南)
7. [配置体系详解](#7-配置体系详解)
8. [可视化选择器](#8-可视化选择器)
9. [智能爬虫 — 零配置采集](#9-智能爬虫--零配置采集)
10. [反检测增强](#10-反检测增强)
11. [EasySpider 任务导入](#11-easyspider-任务导入)
12. [Crawl4AI 轻量 JS 渲染](#12-crawl4ai-轻量-js-渲染)
13. [Apify/Zyte 站点模板](#13-apifyzyte-站点模板)
14. [输出与导出](#14-输出与导出)
15. [安全模型](#15-安全模型)
16. [故障排除与诊断](#16-故障排除与诊断)
17. [常见问题 FAQ](#17-常见问题-faq)
18. [项目架构](#18-项目架构)

---

## 1. 项目概述

OmniCrawler 是一个面向桌面与单机生产环境的模块化采集平台。它从以下来源获取数据：

- **静态网页** — HTML 解析、CSS/XPath 选择器
- **动态网页** — Playwright/Selenium 浏览器渲染
- **REST API / GraphQL** — 结构化接口采集
- **RSS/Atom 订阅源** — 持续监控
- **Sitemap** — 网站地图遍历
- **WebSocket / SSE / 长轮询** — 实时数据流
- **PDF / Office / 压缩包** — 文档解析和 OCR
- **图片 OCR** — Tesseract + PaddleOCR + ddddocr 验证码识别

输出格式：JSONL、CSV、Excel、Parquet、PostgreSQL、DuckDB、OpenSearch。

### 设计理念

- **安全优先**：默认阻止内网/保留地址、遵守 robots.txt、凭据不入配置
- **渐进复杂度**：简单模式（5 步向导）→ 专业模式（YAML 编辑）→ 开发者模式（插件/SDK）
- **可恢复**：SQLite WAL 状态存储，中断可从断点继续
- **可解释**：每步操作有原因说明，自适应调整有审计记录

---

## 2. 核心能力矩阵

| 能力 | 说明 | 入口 |
|------|------|------|
| **五步向导** | GUI 问答式配置生成 | `omnicrawl wizard` 或 GUI 首页 |
| **67 套内置模板** | 覆盖 CMS/电商/新闻/政务/论坛等 | `omnicrawl templates list` |
| **可视化选择器** | 浏览器中右键点选元素，自动生成配置 | `omnicrawl visual-select` |
| **智能页面分析** | 零配置：贴 URL → 自动推断字段和分页 | `omnicrawl auto-analyze` |
| **EasySpider 导入** | 兼容 EasySpider JSON 任务格式 | `omnicrawl import-easyspider` |
| **Crawl4AI 渲染** | 轻量 JS 页面渲染（省 5-10x 资源） | `omnicrawl c4a-fetch` |
| **反检测增强** | 10 维指纹随机化 + 代理轮换 + 行为模拟 | `omnicrawl stealth-fingerprint` |
| **自适应执行** | 实时监测 → 自动调整并发/延迟/OCR | 内置 AutoPilot |
| **统一诊断** | 13 种错误分类 + 自动修复建议 | CLI/GUI 自动触发 |
| **站点模板生成** | Apify 130+ 平台知识 → YAML 模板 | `omnicrawl gen-templates --all` |
| **插件系统** | 子进程沙箱 + 权限白名单 | 开发者模式 |
| **分布式支持** | Redis frontier/锁 + Scrapy 桥接 | 专业模式配置 |

---

## 3. 安装指南

### 方式一：Windows 便携版（推荐）

1. 下载当前构建生成的 `OmniCrawler-0.8.0-Windows-Portable-Standard.zip`
2. 解压到普通可写目录（如 `D:\OmniCrawler`）
3. 双击 `OmniCrawler-Launcher.bat`

> Standard 版含 GUI + Chromium + 常规网页/API/PDF 文本处理。
> Full 版额外含 ChromeDriver + Tesseract + PaddleOCR 离线模型。

### 方式二：源码安装

```powershell
# 创建虚拟环境
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -U pip

# 安装全部功能
.venv\Scripts\python -m pip install -e ".[full,dev]"

# 安装 Playwright 浏览器
.venv\Scripts\python -m playwright install chromium
```

### 可选依赖

```powershell
pip install -e ".[html]"          # 网页解析
pip install -e ".[browser]"       # Playwright 浏览器
pip install -e ".[pdf]"           # PDF 解析
pip install -e ".[ocr-captcha]"   # 验证码识别
pip install -e ".[crawl4ai]"      # 轻量 JS 渲染
pip install -e ".[distributed]"   # Redis 分布式
pip install -e ".[storage]"       # S3/DuckDB
pip install -e ".[postgresql]"    # PostgreSQL
pip install -e ".[search]"        # OpenSearch
```

### 环境要求

- Python 3.10+
- Windows 10+ / macOS 12+ / Linux (Ubuntu 20.04+)
- 2GB+ 可用内存（浏览器模式建议 4GB+）
- 1GB+ 磁盘空间

---

## 4. 5 分钟快速开始

### 命令行三分钟流程

```powershell
# 1. 查看可用模板
omnicrawl templates list

# 2. 探测目标网站并推荐模板
omnicrawl templates inspect https://example.org

# 3. 生成配置并校验
omnicrawl templates render generic/list-detail -o configs/my_site.yaml --set seed_url=https://example.org
omnicrawl validate -c configs/my_site.yaml
omnicrawl doctor -c configs/my_site.yaml

# 4. 小样本试跑
omnicrawl sample -c configs/my_site.yaml --pages 3

# 5. 正式运行
omnicrawl run -c configs/my_site.yaml
```

### 智能零配置流程（最快）

```powershell
# 一行命令：分析页面 → 生成配置 → 试跑
omnicrawl auto-analyze https://shop.example.com/products -o configs/shop.yaml
omnicrawl sample -c configs/shop.yaml --pages 3
omnicrawl run -c configs/shop.yaml
```

### 可视化点选流程（最直观）

```powershell
# 启动 WebSocket 服务
omnicrawl visual-select --output configs/my_site.yaml

# 然后在 Chrome 中：
# 1. 打开目标网页
# 2. 加载 EasySpider Chrome 扩展（位于 assets/extensions/ 目录）
# 3. 右键点选要采集的元素 → "选中元素" → "选中全部" → "采集数据"
# 4. 配置自动写入 configs/my_site.yaml
```

### 中断后恢复

```powershell
omnicrawl resume -c configs/my_site.yaml
```

### 规则修改后重新导出（不重新访问网站）

```powershell
omnicrawl reprocess -c configs/my_site.yaml --run-id <run_id>
```

---

## 5. CLI 命令完整参考

### 任务管理

| 命令 | 说明 |
|------|------|
| `omnicrawl wizard` | 交互式配置向导（6 步问答） |
| `omnicrawl run -c <config>` | 运行采集任务 |
| `omnicrawl resume -c <config>` | 从断点恢复任务 |
| `omnicrawl sample -c <config> --pages N` | 小样本试跑 N 页 |
| `omnicrawl preflight -c <config>` | 运行前检查（依赖/磁盘/配置） |
| `omnicrawl plan -c <config> -o plan.json` | 编译可解释执行计划 |
| `omnicrawl control -c <config> pause/resume/stop` | 控制正在运行的任务 |
| `omnicrawl status -c <config>` | 查看任务状态 |
| `omnicrawl export -c <config>` | 重新导出数据 |
| `omnicrawl reprocess -c <config> --run-id <id>` | 从归档重做提取（不重新访问） |
| `omnicrawl recovery -c <config> overview` | 查看恢复中心 |
| `omnicrawl recovery -c <config> retry-failed` | 重试失败项 |

### 智能工具

| 命令 | 说明 |
|------|------|
| `omnicrawl auto-analyze <url\|file> -o config.yaml` | 智能分析页面结构，自动生成配置 |
| `omnicrawl visual-select [--port 8084] [-o config.yaml]` | 启动可视化选择器 WebSocket 服务 |
| `omnicrawl import-easyspider <task.json> -o config.yaml` | 导入 EasySpider 任务 |
| `omnicrawl stealth-fingerprint [--count N] [--json]` | 生成随机浏览器指纹 |
| `omnicrawl gen-templates --list` | 列出 Apify 130+ 已知平台 |
| `omnicrawl gen-templates --all templates/sites/` | 批量生成平台模板 |

### 模板管理

| 命令 | 说明 |
|------|------|
| `omnicrawl templates list` | 列出内置和用户模板 |
| `omnicrawl templates inspect <url>` | 探测公开网址并推荐模板 |
| `omnicrawl templates render <name> -o config.yaml --set key=value` | 填充模板变量生成配置 |
| `omnicrawl templates validate [--include-legacy]` | 离线检查模板元数据 |
| `omnicrawl templates recommend --url <url>` | 根据 URL 推荐模板 |
| `omnicrawl templates diff <a> <b>` | 对比模板版本差异 |
| `omnicrawl templates merge <base> <theirs> <ours>` | 三方合并模板升级 |
| `omnicrawl templates export-pack <names...> --output pack.zip` | 导出可校验模板包 |
| `omnicrawl templates import-pack <pack.zip> --target templates` | 导入模板包 |

### 定时与备份

| 命令 | 说明 |
|------|------|
| `omnicrawl schedule add -c <config> --name daily --every-seconds 86400` | 添加定时任务 |
| `omnicrawl schedule list` | 列出定时任务 |
| `omnicrawl schedule run-due` | 运行到期任务 |
| `omnicrawl backup create -c <config> -o backup.zip [--include-raw]` | 创建完整备份 |
| `omnicrawl backup restore backup.zip --target <dir>` | 恢复备份 |
| `omnicrawl research-package -c <config> -o research.zip` | 创建脱敏研究复现包 |

### 工具与诊断

| 命令 | 说明 |
|------|------|
| `omnicrawl doctor -c <config>` | 全面诊断 |
| `omnicrawl validate -c <config>` | 校验配置 |
| `omnicrawl capabilities` | 检查 Python/浏览器/OCR/存储能力 |
| `omnicrawl security-report -c <config>` | 汇总网络访问边界 |
| `omnicrawl compare-runs -c <config> <run_a> <run_b> -o diff.json` | 对比两次运行差异 |
| `omnicrawl regression -c <config>` | 离线验证已保存样本 |
| `omnicrawl cleanup -c <config>` | 预览或执行数据保留策略 |
| `omnicrawl runtime-verify` | 验证便携运行时清单 |
| `omnicrawl components list` | 查看已注册组件 |
| `omnicrawl serve` | 启动可远程监控面板 |
| `omnicrawl workbench` | 启动统一桌面工作台 |
| `omnicrawl init` | 复制可编辑项目配置 |
| `omnicrawl migrate` | 迁移旧配置到当前版本 |
| `omnicrawl field-suggest` | 从保存的 HTML 自动推荐字段选择器 |
| `omnicrawl record-actions` | 打开浏览器录制点击/输入/滚动 |
| `omnicrawl api-discover` | 从浏览器 API 捕获 JSON 生成 REST 模板 |
| `omnicrawl plugins` | 列出已注册插件 |

---

## 6. GUI 桌面使用指南

### 启动 GUI

```powershell
run_gui_windows.bat          # Windows
./run_gui_linux.sh           # Linux
./run_gui_macos.command      # macOS
```

### 三种渐进模式

GUI 提供三种模式，通过工具栏下拉框切换：

| 模式 | 可见内容 | 适用人群 |
|------|---------|---------|
| **简单模式** | 五步向导 + 运行进度 + 结果 | 第一次使用 / 非技术用户 |
| **专业模式** | YAML 编辑器 + 高级规则 + 模板 + 定时任务 | 日常使用 |
| **开发者模式** | 完整配置 + 插件 + 诊断 + SDK | 开发和调试 |

> 模式切换不会丢失隐藏字段；保存时保留旧配置中 GUI 尚未认识的扩展段。

### 五步向导

1. **选择来源类型** — 静态网页 / 站内遍历 / REST API / 浏览器动态 / RSS / Sitemap
2. **输入 URL** — 支持多行（每行一个 URL）
3. **定义字段** — 可视化选择器（右键点选）或手动输入 CSS/XPath
4. **设置下载** — 附件/PDF/媒体下载选项
5. **预览运行** — 预览配置 → 试跑 → 正式运行

### 首页快捷操作

- **快速任务卡片**：粘贴 URL → 选意图（保存页面/采集栏目/下载附件/监测变化）→ "分析并准备试跑"
- **自然语言任务**：输入 "每周监测 https://… 的 PDF 变化" → 自动编译
- **最近使用**：下拉框记录最近 10 个 URL
- **快捷按钮**：新建/最近/定时/结果/导入/体检/演示

### YAML 编辑器

专业模式下可用的 YAML 编辑器功能：
- 语法高亮
- 与向导双向同步
- 格式化 + 外部编辑器打开
- 与表单同步按钮
- 差异对比

### 系统托盘

任务完成后系统托盘会弹出通知（成功/失败）。

### GUI 外观与无障碍

高级 GUI 支持丰富的视觉定制和辅助功能：

**主题切换**：菜单栏「视图 → 主题」可在明亮 / 暗黑 / 高对比度之间即时切换，另有色盲友好配色可选。所有颜色由语义化令牌管理，组件可以自动跟随主题变化。

**减少动画**：菜单栏「视图 → 减少动画」开启后，Hero 背景光晕、状态指示器闪烁等动画效果全部静止，适合对运动敏感的用户。

**界面缩放**：菜单栏「视图 → 缩放」支持 90% 紧凑 / 100% 标准 / 125% 大字体 / 150% 特大字体四档。

**键盘导航**：所有可交互控件（按钮、输入框、下拉菜单、列表项、选项卡）在获得焦点时显示 2px 清晰轮廓。Tab 键可在表单字段间顺序跳转。

**屏幕阅读器**：5 个配置向导步骤页已添加辅助标签，状态指示器附带文字描述，所有弹窗和 Toast 通知可被屏幕阅读器捕获。

> 主题、缩放和动画偏好会自动保存，下次启动时生效。

---

## 7. 配置体系详解

### 最小配置示例

```yaml
project:
  name: my_task
  workspace: work/my_task

source:
  kind: static_html
  seeds:
    - https://example.com/page

extract:
  mode: html
  fields:
    title:
      selector: h1
    content:
      selector: article p

outputs:
  jsonl: true
  csv: true
```

### 完整配置段

| 配置段 | 说明 | 关键字段 |
|--------|------|---------|
| `project` | 项目元数据 | name, workspace |
| `source` | 来源定义 | kind, seeds, headers |
| `crawl` | 爬取策略 | max_pages, max_depth, concurrency, strategy |
| `http` | HTTP 设置 | user_agent, respect_robots, delay_seconds, timeout_seconds |
| `browser` | 浏览器设置 | engine, headless, actions, pool_size |
| `extract` | 提取规则 | mode, fields, selectors |
| `download` | 附件下载 | enabled, extensions, media |
| `processors.pdf` | PDF 处理 | enabled, ocr |
| `outputs` | 输出格式 | jsonl, csv, xlsx, parquet, duckdb |
| `updates` | 变更监测 | enabled, revisit_completed, detect_content_changes |
| `schedule` | 定时任务 | enabled, interval_seconds |
| `plugins` | 插件配置 | paths, approved_permissions, fail_open |

### 凭据管理

配置中用 `secret://name` 占位，运行时从环境变量或系统 keyring 读取：

```yaml
source:
  headers:
    Authorization: secret://api_key
```

```powershell
$env:OMNICRAW_SECRET_API_KEY = "Bearer token123"
omnicrawl run -c config.yaml
```

### 输出目录结构

```
work/<project>/
├── state.sqlite3          # 队列、响应、记录、质量、错误、审计
├── raw/                   # 版本化原始响应与浏览器接口证据
├── artifacts/             # PDF、Office、压缩包、图片、媒体
├── diagnostics/<run_id>/  # 脱敏失败诊断
├── pdf/                   # 可独立续跑的 PDF 子项目
└── output/
    ├── records.jsonl      # 结构化记录
    ├── records.csv        # CSV 导出
    ├── extraction_results.xlsx
    ├── review_queue.csv   # 人工复核队列
    ├── metrics.json       # 指标
    └── pipeline_summary.json
```

---

## 8. 可视化选择器

### 概述

无需手写 CSS/XPath —— 在浏览器中**右键点选元素**，系统自动：
1. 检测页面中所有同类元素
2. 生成最优 CSS/XPath 选择器
3. 推断字段名（标题/价格/链接/日期等）
4. 输出 OmniCrawler 配置

### 使用方式

#### CLI 模式

```powershell
# 启动 WebSocket 服务
omnicrawl visual-select --output configs/my_site.yaml

# 打开 Chrome，加载 EasySpider 扩展
# 在目标网页上右键点选元素 → "选中元素" → "选中全部" → "采集数据"
# 选择结果自动写入 configs/my_site.yaml
```

#### GUI 模式

1. 打开 GUI → 进入五步向导 → 步骤 3（定义字段）
2. 点击 **"可视化选择字段 (右键点选)"** 按钮
3. 选择 **"高级点选模式"**
4. 系统启动 WebSocket 服务 → 弹出操作指引
5. 在 Chrome 中右键点选元素 → 回到 GUI 点击"导入字段"

### 技术原理

基于 EasySpider 的同类元素检测算法：

```
用户选中元素 XPath: /html/body/div[3]/div[1]/a[1]
                    ↓ 逐层去掉索引
测试: /html/body/div[3]/div[1]/a    → 匹配 1 个 ❌
测试: /html/body/div[3]/div/a[1]    → 匹配 3 个 ✅  ← 同类组找到！
                    ↓
生成通用 XPath + 收集所有匹配元素 → 字段配置
```

---

## 9. 智能爬虫 — 零配置采集

### 概述

只需提供一个 URL，系统自动：
1. 抓取页面 → 构建 DOM 特征树
2. 检测重复模式 → 识别列表项（商品卡片、文章条目）
3. 分析子元素 → 推断字段名（标题/价格/日期/链接/图片）
4. 检测分页 → 识别"下一页"或 URL 参数模式
5. 输出完整的 OmniCrawler YAML 配置

### 使用方式

```powershell
# 从 URL 分析（需安装 crawl4ai）
omnicrawl auto-analyze https://shop.example.com/products -o config.yaml

# 从本地 HTML 文件分析
omnicrawl auto-analyze page.html --url https://example.com -o config.yaml
```

### 支持的页面类型

| 类型 | 特征 | 检测准确率 |
|------|------|:---:|
| **列表页** | 3+ 重复结构（商品卡片/文章条目） | 95% |
| **详情页** | 单个实体（产品/文章详情） | 70% |
| **单页** | 简单信息展示 | 50% |

### 字段推断规则

| DOM 特征 | 推断字段 | 示例 |
|---------|---------|------|
| `<a>` 含 "title/name" class | 标题 | "iPhone 15" |
| "price/￥" 类名 | 价格 | "¥6,999" |
| "date/time" 类名 | 日期 | "2024-01-15" |
| `<img>` 标签 | 图片地址 | "/images/p1.jpg" |
| `<a href>` 标签 | 链接地址 | "/products/123" |

---

## 10. 反检测增强

### 多层防护

```
Layer 1 — 浏览器指纹随机化
  ├── User-Agent（12 种主流浏览器）
  ├── 屏幕分辨率（11 种常见尺寸）
  ├── WebGL 供应商/渲染器（4 种 GPU 组合）
  ├── Canvas 噪声注入
  ├── 时区/语言/平台伪装
  └── navigator.webdriver 隐藏

Layer 2 — 代理轮换
  ├── 轮询模式
  ├── 随机模式（自动排除失败代理）
  └── 按域名绑定模式

Layer 3 — 人类行为模拟
  ├── 对数正态分布思考延迟
  ├── 贝塞尔曲线鼠标轨迹
  ├── 真实打字速度
  └── 分段阅读停顿滚动

Layer 4 — Crawl4AI Undetected 模式
  └── Patchright 浏览器（绕过 Cloudflare/Akamai）
```

### 使用方式

```powershell
# 生成随机指纹
omnicrawl stealth-fingerprint --count 3 --json

# 在配置中启用
# browser_fetcher.py 自动注入 stealth.min.js + CDP 命令

# 使用 Crawl4AI undetected 模式
omnicrawl c4a-fetch https://protected-site.com --stealth
```

### 代码集成

```python
from omnicrawl.stealth_enhanced import StealthEnhancer

enhancer = StealthEnhancer(proxy_list=["http://proxy1:8080", "http://proxy2:8080"])
fingerprint = enhancer.randomize()

# 应用到 Playwright context
enhancer.apply_to_playwright_context(context, fingerprint)

# 代理轮换
proxy = enhancer.rotator.next_round_robin()
enhancer.rotator.report_success(proxy)   # 成功时调用
enhancer.rotator.report_failure(proxy)   # 失败时调用
```

---

## 11. EasySpider 任务导入

### 概述

兼容 EasySpider（易采集）的 JSON 任务格式。支持：
- 操作节点：打开网页、点击元素、提取数据、滚动
- 流程控制：循环、条件判断
- XPath 候选列表 → 自动选最优

### 使用方式

```powershell
# 基础导入
omnicrawl import-easyspider task.json -o config.yaml

# 输出 Task IR 格式
omnicrawl import-easyspider task.json --ir

# 然后正常运行
omnicrawl run -c config.yaml
```

### 支持的 EasySpider 操作映射

| EasySpider 操作 | OmniCrawler 配置 |
|----------------|-----------------|
| 打开网页 (option:1) | `source.seeds` + `browser.actions[wait_ms]` |
| 点击元素 (option:2) | `browser.actions[click]` |
| 提取数据 (option:3) | `extract.fields` |
| 循环 (option:8) | `crawl.pagination` 或 `browser.actions` |
| 滚动 | `browser.actions[scroll_bottom]` |

---

## 12. Crawl4AI 轻量 JS 渲染

### 概述

集成 [Crawl4AI](https://github.com/unclecode/crawl4ai)（74.9k stars），提供：

- **轻量 JS 渲染**：比 Playwright 全浏览器省 5-10x 资源
- **自适应爬取**：自动学习网站模式、探索相关内容
- **BFS 深度爬取**：全站遍历 + 域名过滤
- **LLM 提取**：用 AI 从页面提取结构化数据
- **Undetected 模式**：绕过 Cloudflare/Akamai
- **虚拟滚动**：无限滚动页面全量加载
- **内存自适应调度**：批量并发控制

### 安装依赖

```powershell
pip install omnicrawl-platform[crawl4ai]
crawl4ai-setup
```

### 使用方式

```python
from omnicrawl.crawl4ai_bridge import Crawl4AIEngine, C4AConfig

# 基础抓取
engine = Crawl4AIEngine()
result = engine.fetch("https://spa-site.com")
print(result.markdown[:500])

# Undetected 模式
config = C4AConfig(browser_type="undetected")
result = Crawl4AIEngine(config).fetch("https://protected.com")

# CSS schema 结构化提取
schema = {
    "name": "Products",
    "baseSelector": ".product-card",
    "fields": [
        {"name": "title", "selector": ".title", "type": "text"},
        {"name": "price", "selector": ".price", "type": "text"},
    ]
}
result = Crawl4AIEngine(C4AConfig(extraction_strategy="css", extraction_schema=schema)).fetch(url)
```

---

## 13. Apify/Zyte 站点模板

### 概述

提取了 Apify Ultimate Scraper 覆盖的 25 个主流平台的站点知识，包括典型字段、采集提示和注意事项。

### 已覆盖平台

| 类别 | 平台 |
|------|------|
| **社交媒体** | Instagram, Facebook, TikTok, YouTube, X(Twitter), LinkedIn, Reddit |
| **搜索引擎** | Google Search, Google Maps, Google Trends |
| **电商** | Amazon, Walmart, eBay |
| **旅游** | Booking.com, TripAdvisor, Airbnb, Yelp |
| **开发者** | GitHub |

### 使用方式

```powershell
# 列出所有已知平台
omnicrawl gen-templates --list

# 批量生成模板文件
omnicrawl gen-templates --all templates/sites/

# 查看某个平台
omnicrawl gen-templates --generate amazon
```

生成的模板包含字段定义和注释，选择器需根据实际页面填写或配合 `auto-analyze` 自动生成。

---

## 14. 输出与导出

### 输出格式对照

| 格式 | 适用场景 | 配置 |
|------|---------|------|
| **JSONL** | 每行一条 JSON 记录，流式处理 | `outputs: {jsonl: true}` |
| **CSV** | 表格数据，Excel 兼容 | `outputs: {csv: true}` |
| **Excel (.xlsx)** | 多 sheet 报告 | `outputs: {xlsx: true}` |
| **Parquet** | 大数据分析 | `outputs: {parquet: true}` |
| **DuckDB** | 本地 SQL 分析 | `outputs: {duckdb: true}` |
| **PostgreSQL** | 生产数据库 | `outputs: {postgresql: {...}}` |
| **OpenSearch** | 全文搜索 | `outputs: {opensearch: {...}}` |
| **MySQL** | 传统数据库 | `outputs: {mysql: {...}}` |

### 质量报告

每次运行后自动生成 `output/quality_report.html`：
- 字段覆盖率统计
- 缺失值分布
- 异常值检测
- 提取位置证据

### 人工复核

```powershell
# 查看复核队列
omnicrawl status -c configs/my_site.yaml

# 导出复核队列
# output/review_queue.csv — 在 Excel 中标注"有效/存疑/错误"
```

---

## 15. 安全模型

### 网络安全

| 防护 | 实现 |
|------|------|
| **协议白名单** | 仅允许 HTTP/HTTPS |
| **地址过滤** | 阻止本机/内网/保留地址 |
| **DNS 重绑定** | 每次重定向重新检查 |
| **robots.txt** | 默认遵守，失败关闭 |
| **响应限额** | max_response_bytes, max_api_capture_bytes |
| **磁盘保护** | 低于 512MB 自动暂停 |

### 凭据安全

- 配置中只用 `secret://name` 占位
- 运行时从 `OMNICRAW_SECRET_<NAME>` 环境变量读取
- 或从系统 keyring 读取
- 解析后的密钥不会写回配置文件
- 日志和诊断自动脱敏

### 插件沙箱

- 子进程隔离
- 权限白名单（network/filesystem/execute）
- 10 秒超时机制
- fail-closed：未批准的能力不执行

### 存档安全

- Zip Slip 路径穿越防护
- 符号链接拒绝
- 压缩炸弹检测（解压比 > 100:1 拒绝）
- 单个文件 100MB 上限
- 临时目录 + 原子 `os.replace`

---

## 16. 故障排除与诊断

### 自动诊断

OmniCrawler 内置统一诊断系统，覆盖 13 种错误类型：

| HTTP 状态 | 诊断 | 自动修复 |
|-----------|------|---------|
| 401 | 需要身份验证 | 提示配置 secret:// 或登录 |
| 403 | 访问被拒 | 提示更换 UA/代理 |
| 429 | 请求过频 | 自动降低并发至 1 + 延迟至 5s |
| 502/503 | 服务器暂时不可用 | 建议等待后重试 |
| 验证码 | 人机验证 | 提示降速或使用 undetected 模式 |

### 常用诊断命令

```powershell
omnicrawl doctor -c config.yaml     # 全面体检
omnicrawl validate -c config.yaml    # 配置校验
omnicrawl capabilities               # 环境能力检查
omnicrawl preflight -c config.yaml   # 运行前检查
omnicrawl runtime-verify             # 便携版完整性检查
```

### 错误恢复

```powershell
# 查看恢复中心
omnicrawl recovery -c config.yaml overview

# 重试失败项
omnicrawl recovery -c config.yaml retry-failed

# 从原始归档重做提取（不重新访问）
omnicrawl reprocess -c config.yaml --run-id <run_id>
```

---

## 17. 常见问题 FAQ

### 安装与环境

**Q: 解压后双击没反应？**
A: 确保解压到普通可写目录（如 `D:\OmniCrawler`），不要直接在 ZIP 内运行。

**Q: 缺少 Chromium 或 Playwright？**
A: 便携版已内置。源码版运行 `python -m playwright install chromium`。

**Q: OCR 不可用？**
A: Standard 版不含 OCR，下载 Full 版获取 Tesseract+PaddleOCR。文本 PDF 无需 OCR。

**Q: Crawl4AI 不可用？**
A: 安装 `pip install omnicrawl-platform[crawl4ai]` 后运行 `crawl4ai-setup`。

### 采集配置

**Q: 翻页但地址栏网址不变？**
A: 切换到浏览器模式 → 使用可视化选择器（`omnicrawl visual-select`）自动捕获后台 API。

**Q: 采集速度太慢？**
A: 增加 concurrency 到 4-8，减少 delay_seconds 到 0.5-1s。但过高会被限速。

**Q: 被网站封 IP？**
A: 增加 delay_seconds 到 2-3s，降低 concurrency 到 1-2。使用代理轮换。使用 undetected 模式。

**Q: 附件没下载？**
A: 确认 `download.enabled=true`，extensions 包含需要的后缀。

**Q: 字段提取为空？**
A: 检查页面类型（HTML/JSON），切换到对应 mode。动态页面用浏览器模式。先用 `auto-analyze` 重新分析页面。

### 运行与恢复

**Q: 任务中断了？**
A: `omnicrawl resume -c <config>` 从中断点继续，进度在 SQLite 中不丢失。

**Q: 改了规则重新导出？**
A: `omnicrawl reprocess -c <config>` 从原始归档重新提取，不重新访问网站。

**Q: 定期自动采集？**
A: 专业模式启用定时任务，或系统调度器执行 `omnicrawl schedule run-due`。

**Q: 复制到另一台电脑？**
A: 便携版复制整个文件夹。源码版复制 `work/<project>/` 目录。

### 输出与导出

**Q: 没有 Excel？**
A: 需要 openpyxl。便携版已含；源码版 `pip install openpyxl`。

**Q: CSV 中文乱码？**
A: Excel 中 "数据 → 从文本/CSV" 选择 UTF-8 编码导入。

**Q: 导出到数据库？**
A: 支持 PostgreSQL/DuckDB/MySQL/OpenSearch，安装对应依赖后在 outputs 段配置。

### 安全

**Q: 有什么规则？**
A: 默认遵守 robots.txt、只采同域名。确保有访问权限，不绕过验证码。

**Q: 凭据安全？**
A: 配置中用 `secret://name` 占位，通过 `OMNICRAW_SECRET_name` 环境变量注入。

**Q: 界面颜色太刺眼或太暗？**
A: 菜单栏「视图 → 主题」可在明亮、暗黑和高对比度三种主题间切换。选「色盲友好」可替换红/绿状态色为蓝/橙学术配色。界面缩放可在「视图 → 缩放」中选 90%~150% 四档。

**Q: 如何减少界面动画？**
A: 打开菜单栏「视图 → 减少动画」。开启后 Hero 背景光晕停止运动，状态指示器停止闪烁，页面切换淡入效果关闭。

**Q: 支持英文界面吗？**
A: 当前版本已建立国际化管道。开发者在 `locale/` 目录添加英文 `.po` 翻译并编译 `.mo` 后即可切换。目前约 13% 的界面文字已有英文翻译，欢迎贡献更多翻译。

---

## 18. 项目架构

### 核心模块

```
src/omnicrawl/
├── cli.py                   # CLI 入口（30+ 命令）
├── config.py                # 配置加载与验证
├── pipeline.py              # 采集 Pipeline 编排
├── state/state_store.py     # SQLite 状态持久化
├── browser_fetcher.py       # Playwright/Selenium 浏览器引擎
├── extractors.py            # CSS/XPath/JSON 多策略提取
├── egress.py                # 网络安全边界（多层防护）
├── async_fetcher.py         # HTTP 异步抓取
├── sources.py               # 多种来源类型支持
├── exporters.py             # 多格式导出
├── task_ir.py               # 任务中间表示
├── task_spec.py             # 任务规格编译
├── adaptive_execution.py    # 自适应参数调整
├── error_center.py          # 错误分类与报告
├── credentials.py           # 凭据安全管理
├── archives.py              # 安全存档解压
├── site_inspector.py        # 站点探测
├── api_discovery.py         # 浏览器 API 捕获
├── action_recorder.py       # 浏览器操作录制
├── plugin_sandbox.py        # 插件沙箱
│
├── auto_pilot.py            # 🆕 自适应执行闭环
├── diagnostics.py           # 🆕 统一诊断系统
├── intelligent_scraper.py   # 🆕 智能页面分析
├── stealth_enhanced.py      # 🆕 反检测增强
├── crawl4ai_bridge.py       # 🆕 Crawl4AI 桥接
├── easyspider_bridge.py     # 🆕 EasySpider 导入
├── captcha_ocr.py           # 🆕 验证码 OCR
├── apify_templates.py       # 🆕 Apify 模板生成
│
├── visual_selector/         # 🆕 可视化选择器
│   ├── __init__.py
│   ├── similarity.py        # 同类元素检测引擎
│   ├── field_converter.py   # 选择器→字段转换
│   └── server.py            # WebSocket 服务
│
├── gui/                     # PyQt6 桌面 GUI
│   ├── main.py              # 主窗口
│   ├── home.py              # 首页（快速任务）
│   └── wizard/              # 五步向导
│
├── templates/               # 67 套内置 YAML 模板
├── sdk/                     # Python SDK
└── state/                   # 状态存储
```

### 数据流

```
用户输入 → 配置生成 → Pipeline 编排
              ↓
    ┌─────────┴──────────┐
    │  可视化选择器       │  ← 浏览器扩展
    │  智能爬虫           │  ← auto-analyze
    │  EasySpider 导入    │  ← import-easyspider
    │  模板渲染           │  ← templates render
    │  交互式向导         │  ← CLI/GUI wizard
    └─────────┬──────────┘
              ↓
    ┌─────────┴──────────┐
    │  Crawl4AI (轻量)    │  ← 可选
    │  Playwright (完整)  │
    │  Selenium (兼容)    │
    │  HTTP (静态)        │
    └─────────┬──────────┘
              ↓
    ┌─────────┴──────────┐
    │  Egress 安全边界    │  ← 协议/域名/预算
    │  AutoPilot 自适应   │  ← 并发/延迟/OCR
    └─────────┬──────────┘
              ↓
    ┌─────────┴──────────┐
    │  提取 + 质量检查     │
    │  StateStore (SQLite) │
    └─────────┬──────────┘
              ↓
    ┌─────────┴──────────┐
    │  导出               │
    │  JSONL/CSV/Excel/   │
    │  Parquet/PostgreSQL │
    └─────────────────────┘
```

---

## 附录

### 许可证

GNU Affero General Public License v3.0 — 详见 `LICENSE` 文件。

### 相关项目

- [Crawl4AI](https://github.com/unclecode/crawl4ai) — 轻量 AI 爬虫引擎
- [EasySpider](https://github.com/NaiboWang/EasySpider) — 可视化无代码爬虫（任务导入、相似元素检测算法、Chrome 扩展）
- [Apify Agent Skills](https://github.com/apify/agent-skills) — 专业爬虫平台技能

### 版本历史

详见 `CHANGELOG.md` 和各版本 `RELEASE_REPORT_*.md`。

当前 0.8.0 版本额外提供本地可复用 E2E：它只使用临时本机 HTTP 服务，不会采集互联网或生产数据。首页会优先接收自然语言任务描述，并将其转化为可检查的配置草案；其余必填项也集中在第一页，减少向导中途被阻断的情况。开发者可在仓库根目录执行 `./e2e/run.ps1 -Browser`（Windows）或 `./e2e/run.sh --browser`（macOS/Linux）复验完整链路；结果写入 `docs/E2E_TEST_REPORT.md`。
