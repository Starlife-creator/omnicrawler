# 代表性任务验收账本

> 适用版本：0.12.0 · 逐条证据截至 2026-09-16（**0.13.0 / 0.13.1 未重跑验收**）· 建立日期：2026-09-12 · 基准提交：`13ea424` · 维护状态：现行

本账本记录“当前版本真实证明了什么”。历史报告不自动等于当前版本通过；没有当前证据的
能力标为未知。每次执行必须使用独立 workspace 与 data_dir，恢复测试只复用本任务状态。

| 工作流 | 固定输入与正确答案 | 当前自动证据 | 当前状态 | 下一项验收 |
|---|---|---|---|---|
| 静态列表→详情→结构化结果 | `quality_benchmark.py` 的 `list-three-details`，6条记录、title/price、逐条来源路径 | 本地HTTP真实流水线；精确评分含漏采、错值、错来源、额外及重复记录；**2026-09-13 新增 GUI 闭环两条**：`test_gui_worker_local_task.py` 经真实 `WorkerTaskRunner` + 真实 worker 子进程（pid≠当前进程、事后退出无残留）跑本地站点——① 单页列表恰好3条真值+CSV；② **列表→详情两级 4 页 6 条**、来源覆盖 4 个 URL、**XLSX 可重新打开** | 已验证（JSONL/CSV/XLSX 核心链，含 GUI 子进程、两级抓取与**真实运行按钮入口**） | 便携产物内复跑（按约定留 CI / 受控环境） |
| 动态页面→分页/滚动→去重 | **程序自造的固定样例**：3 页 × 4 条，条目由页面**内联 JS 注入**（只有真渲染才看得到），页面间用静态 `rel="next"` 相连；真值 = 12 条且互不相同 | **2026-09-13 新增端到端**（`tests/integration/browser/test_dynamic_pagination_fixed_sample.py`）：本地 JS 站点经真实流水线（浏览器引擎）⇒ `processed=3`（首末页齐全）、12 条、**无重复**、来源覆盖 3 个页面；**浏览器进程回收**独立成一条用例核对。**独立性核对**：断掉"浏览器源链接发现"后 `processed` 由 3 掉到 1（证明断言承重）。门禁 `OMNICRAWL_BROWSER_TESTS=1`（与仓库既有浏览器用例同款），跳过时给出安装命令。**2026-09-13 再补滚动样例**（`test_infinite_scroll_fixed_sample.py`）：单页 3 批 × 4 条，内容**只在页面被滚动过之后**才追加 ⇒ `processed=1` 且 12 条、无重复；**去掉滚动动作只剩首屏 4 条**（承重性做成常驻用例，非一次性核对）。样例刻意**不依赖 scroll 事件 / IntersectionObserver**（无头下二者派发不稳定，见缺口节） | 已验证（**分页 + 滚动**） | 便携产物内复跑（按约定留 CI / 受控环境） |
| API→游标分页→增量 | 本地HTTP固定3页：首次交付ID 1/2/3；二次仅末页值变化，应只交付ID 3新值 | 自动分析按正式JSONPath试跑；真实流水线核对游标替换、末页停止、来源URL及二次同步；恢复保留未完成游标；**2026-09-13 新增 GUI 路径**：同场景经真实 GUI 运行器 + worker 子进程跑通（3 条含游标来源、游标链恰好一遍、二次只交付变化那条）；**2026-09-14 新增「经 GUI 表单创建」**：`test_form_created_cursor_api_task_runs_end_to_end` —— **空白表单**选「API / JSON」+「按游标」分页、填记录路径与字段 → 保存 → 运行 → 三页全交付、游标链恰好一遍、二次只交付末页变化那条（与 YAML 路径同一组真值） | 已验证（JSONL核心链，含 GUI 运行路径与**表单创建**路径） | 便携产物内复跑（按约定留 CI / 受控环境） |
| 附件→PDF/OCR→复核（**自动部分**） | 程序自造样本（零第三方内容）：数字版=reportlab 中英文+表格；图片版=PIL 渲染的**无文字层** PDF（用来逼出 OCR）。真值：`HT-2026-0001` / `示例服务合同` / `12,345.67 元` | **2026-09-13 新增端到端**（`tests/integration/pdf/test_pdf_ocr_review_workflow.py`）：① 数字版取到中英文与表格（还原为 Markdown 表）、金额标准化为「元」、字段带**页码 + 原文证据**、校验 `valid`；② 图片版 `parse_method=ocr`、`ocr_status=done`、置信度 > 0.5（用**项目内置 tesseract** `.runtime/tesseract/`），OCR 文本还原出编号与名称，宽容模式抽取 `valid`，**金额经结构判定恢复为真值并逐字符核对**（`12.345.67` → `12345.67`；原始值仍保留）；③ 故意用严格模式制造失败 → `invalid`/`needs_review` + 校验信息说明缺哪些必填字段 → 复核队列 → 人工补齐并确认 → `apply-review` 写入 `human_review` 值 → **再导出可见** | 已验证（自动部分） | — |
| 附件→PDF/OCR→复核（**人工部分**） | 同上样本与真值 | 无自动证据（按定义不可自动） | 未知（须人工走查） | 用**可复用走查包**走一遍：`python tools/walkthrough_env.py`（固定样本 + 隔离工作区）配合 `docs/MANUAL_WALKTHROUGH.md` 的 8 步清单，结论填《审查记录》§二十。样本期望值由 `tools/walkthrough_demo_site.py` 的 `EXPECTED` 提供并被 `tests/unit/tools/test_walkthrough_demo_site.py` 锁定（不会与文档漂移） |
| 定期采集→变更检测→差异导出 | 本地固定两版输入：v1=甲乙丙；v2=甲改价、乙不变、丙移除、丁新增 | **2026-09-13 新增端到端**：经真实 GUI 运行器 + worker 子进程连跑三次——v2 报 `modified=1`、`added=1`（中文键身份生效）；「删除」经 `run_compare` 检出 `removed=1`、`possibly_removed=0`；**差异导出**经产品自身 `compare-runs -o <file>` 写出文件且计数一致；第三次内容不变 → **零差异（无假差异）** | 已验证（变动识别 + 删除 + 差异导出 + 无假差异；删除走 run_compare 而非主路径） | `updates.confirm_missing_runs` 的连续确认（见缺口）；首次同步是否应报变更（待决策） |
| 长任务→中断→恢复 | 故障注入与取消恢复用例已有；**2026-09-13 新增 GUI 路径**：慢站点（列表 + 8 详情），运行中经 GUI 停止 | 真实 GUI 运行器 + worker 子进程：**取消时服务端仅命中 3/9 页（对照实验：不取消 = 9/9）**、后端 `status=cancelled` 且 `pending>0`（证明确为中途停止）；停止后子进程回收无残留；重启后按标题去重**无重复**、两次运行合起来**覆盖全部页面** | 已验证（GUI 停止真实有效 + 进程回收 + 重启不重复不遗漏）；「resume 只交付剩余」由 ApplicationService 级用例覆盖 | 便携产物内复跑 |

## 当前已知缺口

- **单页模式（详情页）曾静默丢短值，且留下一个更糟的脏值**（2026-09-14 实测 → 同日修）。
  旧实现用一条 `len(text) < 5` 同时干两件事（挡 UI 装饰 + 挡噪声），结果误伤数据。实测一个普通商品详情页：
  `<h1>示例商品</h1>`（4 字**标题**）被丢 ⇒ **标题字段整个没有**；`9`（价格）与 `元`（单位）被丢，反倒是父节点
  `div.price` 的**合并文本** `'9\n    元'` 当了「价格」（交付脏值）；第二个 `<li>` 被**按名字去重**挤掉。

  **已修**（`extraction/intelligent_scraper.py::_infer_single_page_fields`）：长度规则＝**必须有可取值字符**（数字/字母/汉字）——
  长度不是「是不是数据」的判据；UI 装饰改由**区域**判据挡（复用同模块 `_is_chrome_path`：aside / nav / footer）；
  去重键＝`(字段名, CSS 路径)`（同名不同选择器都保留；同一选择器只出一个——取值相同，重复列无意义）；
  容器（文本＝其直接子元素文本拼接）跳过，取更精确的子元素。

  证据：7 条用例（短值保留 / 不用容器合并值 / 区域仍挡装饰 / 同名不同选择器都留 / 纯标点不算字段 / 上限生效 / 无重复模式走同一路径）
  + **五处注入承重核对**（长度退回旧口径、区域判据失效、可取值字符判据失效、容器判据失效、去重退回只用名字 → 对应用例分别判红）。

- **PDF/OCR 侧此前没有「专项判据」**（2026-09-14 补齐 → **同日闭环**）。
  原状：crawler 侧有质量基准（精确结构化：取值要与真值逐字相等），而 PDF 侧只有**通过性**集成测试，
  没有「分数 + 门槛 + 反例集」——「模糊抽取抽到了什么」与「该复核的有没有进复核」都没被量化。

  **已补** `services/pdf_quality_benchmark.py`：数字版 + 图片版两用例（样本程序自造、离线、可复现），
  判据＝**要么对，要么进复核**（归一后逐字比对 / 每字段带页码与原文证据 / 低置信必须 `needs_review` /
  错值不得被 `auto_accepted` 放行），与 crawler 侧**分开记分**。

  **反例集（11 条单测）**：删一条 / 值错 / 缺来源证据 / 低置信不标复核 / 自报高置信但值错 / 空记录 /
  阈值可配 / 归一不得掩盖不可判定的误读（`12345.67` vs `1234567` 必须判错）。
  **承重性**：把三条判据分别打坏，对应用例均判红（已实测）。

  **同批发现（特征化用例已钉住，待拍板）**：图片版经真 OCR 后汉字间空格会**原样进入交付值**且
  `auto_accepted`（`示例服务合同` → `示例  服务  合同`，置信度 0.98）⇒ 在本判据下即「错值自动放行」；
  产品已有逐字段开关 `collapse_whitespace`（默认关）。

- **表单曾无法 author `source.pagination`，且分页的形状知识散在三处**（2026-09-14 收拢 → **同日已闭环**）。
  原状：`core/config.py` 只校验 `type == page`；`sources/sources.py` 消费 `type == page` 与
  `next_path`；`extraction/api_discovery.py` 产出 `page` / `next` / `cursor` 三种形态。
  于是 **GUI 没有任何分页入口** —— 表单建出的 API 任务只能抓第一批结果，**且没有任何提示**；
  更糟的是游标配置缺 `next_path` 时**既不报错也不翻页**，运行成功、产物看起来正常，只是少了后几页。

  **已收拢**：新增 `core/pagination.py` 作为分页的**唯一真源**（形状 + 字段 + 校验），
  三方改为共用它：
  - `core/config.py` 的校验改为 `validate_pagination()` ⇒ 顺带补上游标侧：缺 `next_path` 现在**加载时即报错**；
  - GUI 由契约**渲染**控件（`高级设置 → 分页方式`：不翻页 / 按页码·偏移 / 按游标·下一页值），
    字段名与取值类型全部来自契约，GUI 只保留中文标签（表现层）⇒ **不再有第二份分页词典**；
  - 判定形状用 `detect_shape()` 而不是读 `type`：既有游标配置（`{next_path, parameter}`，无 `type`）
    与基准任务都属这种写法，判定条件刻意与**取数引擎的生效条件**对齐（死键不报错）。

  **两条防再分叉的机器检查**（`tests/unit/core/test_pagination_contract.py`）：
  ① 契约 → 引擎：每个声明形状都在真实引擎上验一次（页码 ⇒ `seed()` 展开，游标 ⇒ `_discover_api_next()` 替换参数）；② 引擎 → 契约：**扫描 `sources.py` / `pipeline/_run.py`**
  里 `pagination.get("…")` 的键，断言全部在契约里声明过（先断言「确实扫到了」，防假通过）。

  **GUI 侧的证据**：`test_form_authored_pagination_reaches_config_and_survives_extras`（换形状＝该组参数重填，避免把页码参数名 `page` 带到游标形状写成 `?page=<游标值>`；契约外/表单不渲染的键如 `location` 表单同步后仍在；未知形状 `type: scroll` 进「保持原样」不改写）；`test_form_created_cursor_api_task_runs_end_to_end`（空白表单 → 保存 → 运行 → 真值）；`tests/unit/config/test_gui_config_preservation.py` 两条往返用例（含历史无 `type` 写法）。
  **承重性核对（三处注入，全部会红）**：① 让 `_pagination_from_form()` 恒返回 `None` ⇒ 端到端用例失败（只抓第一批）；② 清空 `_pagination_extras` ⇒ 契约外键保留用例失败；③ 把契约校验打空（退回只认 `type=page`）⇒ 校验用例 `DID NOT RAISE` 而失败。

  **仍未闭环（如实登记）**：`location: body` 需要 `source.payload` 配套，表单**仍不渲染**它
  （契约里标 `editable=False`，原样透传）；`extract.mode`/`source.kind` 的表单入口属另一条
  （见下方 GUI 条目）。

- **「release 产物依赖与锁一致」此前只覆盖可安装性**（2026-09-14 补上**严格口径**）。
  原状：`uv sync --locked` 成功 + 可导入即算闭环，**没有核对产物自己声明了什么** ——
  而 `[project.optional-dependencies]` 与构建后端都可能把 extras 丢掉或改名，
  pyproject↔lock 的同步检查（`check_lockfile_consistency.py`）看不到产物这一层。

  **已补**：`tools/check_release_integrity.py` 新增 `check_wheel_requirements_against_lock()`，
  把 wheel 的 `Requires-Dist` 与 `uv.lock` 里项目自身的 `requires-dist` **逐条对账**
  （不依赖 `packaging`：锁里每条自带 `specifier`，归一后逐条相同即已保证版本满足约束）。
  CLI 新增 `--lock`（默认 `<project_root>/uv.lock`）⇒ **CI 里已有的**
  `check_release_integrity.py --wheel-dir dist` 步骤**自动获得该检查**，无需改 workflow。

  归一规则（缺一条就会误报，实测各踩一次）：① 包名 PEP 503 归一；② 版本约束**按子句排序**
  （产物 `PyYAML<7,>=6` vs 锁 `>=6,<7`）；③ 去掉 `name[extra]` 的方括号段但**保留其后约束**
  （`psycopg[binary]>=3.2,<4`，早先按 `split("[")` 会丢掉约束）；④ marker 的
  `python_version` 与 `python_full_version` 视为等价（uv 会改写）；⑤ 兼容 PEP 508 与旧式
  `name (spec)` 两种渲染。

  证据：`tests/unit/utils/test_release_integrity.py` 新增 7 例（格式差异不算不一致、方括号、
  marker 归一、约束改动**两向都报**、版本不一致、缺锁文件不许静默、不传 `lock_path` 时向后兼容）。
  **实测**：对本仓库真实构建的 wheel 对账 **0 问题**；把锁里一个 specifier 改一个字后
  CLI **退出码 1** 并给出"产物声明了锁里没有的依赖 / 锁里有的依赖产物没声明"两向提示。

  **顺带发现（维护者体验，未修）**：`pip wheel .` 在同一工作树里**第二次**会失败 ——
  setuptools 复用的 `build/` 里已有上次的 `.dist-info`，报 `[WinError 183] 当文件已存在时，
  无法创建该文件`。`rm -rf build` 后正常。CI 每次是干净检出，因此不受影响。
- **质量基准曾只有 1 个任务、1 种形态**（2026-09-14 扩到 **4 个任务 / 3 种取数形态**）。
  原状：`services/quality_benchmark.TASKS` 只有 `list-three-details`（HTML 列表 + 详情链接）；
  分页**配置**、API 通道、单页卡片这些形态在基准里**没有任何代表** —— 而"可复现的任务基准"
  正是 §1.2 采集能力的验收方式，只有一种形态等于只有一种证据。

  **已扩**（`BenchmarkTask` 增加形态声明：`source_kind` / `extract_mode` / `item_path` /
  `content_type` / `pagination`；**默认值 = 原 HTML/爬取形态 ⇒ 既有任务零行为变化**）：

  | 任务 | 形态 | 覆盖点 |
  |---|---|---|
  | `list-three-details`（原有） | `crawl` + HTML | 列表 + 站内链接到详情页 |
  | `list-two-pages` | `crawl` + HTML + `source.pagination{type=page}` | **翻页配置**通路、跨页总数正确 |
  | `api-cursor-two-pages` | `rest` + `json` + `pagination{next_path,parameter}` | **API 取数通道**、游标推进到末页即停 |
  | `card-price-outside-link` | `static_html` + HTML | **单页不跟随链接**、字段分散在链接内外（价格在 `<a>` 之外） |

  分页键名**原样透传**给 `source.pagination`（不在基准里重新编码，避免第二份分页词典）；
  json 任务的页面由本地服务按 `application/json` 提供。四个任务实测**全部满分**
  （完整性/准确性/来源证据 = 1.0，意外与重复 = 0）。

  **判据侧**（上一批 `c64e709` 已闭环，本次未动）：反例集 12 例 —— 删一条 / 重复一条 / 加错记录 /
  填错字段 / 缺 `source_url` / 错来源路径 / 错来源域 / 缺字段却自报满分 / 匹配与记录顺序无关 …
  任一都必须让 `ok=False`。

  **覆盖面成为机器检查**：新增单测断言 `extract_mode` 集合、`source_kind` 集合、分页任务与多页任务
  都**有代表** —— 删掉 API 任务会被判红。避免"覆盖了哪些形态"退化成口头声明。

  **仍未闭环（如实登记）**：
  - **OCR / 模糊抽取的专项标准**未做：它属 **pdfx 侧**的判据（crawler 基准混不进 OCR，
    混进去就会变成"用精确结构化标准要求模糊抽取"）；`TASKS` 里也还没有"附件 / PDF"形态。
- **GUI 此前完全用不到产品自带的 DOM 分析器**（2026-09-14 补上「分析页面并填字段」）。
  在此之前，GUI 里唯一能"补字段"的入口是「启发式补全字段」，它只追加**通用**规则
  （`h1`/`a`/`time`/`.author`/`.description`，与目标页无关）；而 CLI 的 `analyze` 一直能用
  同一套分析器推断出真实容器与选择器 —— **能力存在，但 GUI 没有通路**。

  **已补**：
  - 表单字段区新增主按钮「**🔍 分析页面并填字段**」（原「启发式补全字段」降为离线后备）；
  - 抓取走 `sources/site_inspector.fetch_page_html` —— **SSRF / 重定向 / 大小 / robots 守卫与 crawl 完全一致**
    （顺带把 `inspect_url` 的守卫配置抽成 `build_inspection_config`，避免第二条无守卫抓取路径）；
  - **沿用任务自身的出网策略**（`allow_private_network` / `robots_fail_closed`）：分析不比运行更宽松，也不更严格；
  - **失败不假装成功**：如实报错，并用 Toast 的 action 给出「改用通用规则」的去路；
  - **容器落在页面框架时不填**：复用分析器自己的 `_is_chrome_path`（aside/nav/footer）判据，
    只在导航/侧边栏里找到重复元素时**不填容器并警告**（避免把侧边栏当列表交给用户）；
  - 只**追加**不覆盖（与启发式补全同一 Upsert 语义）；结果经控件写入 ⇒ 自动标脏 ⇒ 试跑闸门失效。

  **证据**：`tests/integration/test_gui_form_create_task.py` 增至 11 例，其中三条新用例：
  ① 列表页 ⇒ 填入真实容器 `… div.item` 与真实选择器 `h2.t`/`span.p`（**不是**通用规则）；
  ② 只有侧边栏的页面 ⇒ **不填容器** + 明确警告；③ 没有网址 ⇒ 只提示、不抓取、不假装成功。
  **承重性核对**：把 `apply_analysis` 改成空实现 ⇒ 用例①失败；把 `_is_chrome_path` 改成恒假 ⇒ 用例②失败。

  **过程教训（门禁抓到我自己）**：新按钮要配帮助条目，而 `get_help()` 对未知 ID 会抛 `KeyError`，
  于是先在 `HELP_ENTRIES` 里加了 `fields.analyze_page` —— 但**只加条目、没把 `HelpTooltip` 绑上界面**，
  被 `tests/integration/template/test_help_ux.py` 判红（它断言"每个声明的帮助条目都真的挂在界面上"）。
  这已是本项目第 N 次抓到"定义了没有生产者"；本次由门禁在合并前拦下，未逃逸。

  **进一步（2026-09-14）**：**`source.kind` / `extract.mode` / `item_path` 也补上了表单入口**，
  于是"从零在表单里建 API 任务"成立（此前只能建网页任务）：
  - 意图区新增「**数据来源**」下拉（网页（静态单页）/ 网页（跟随站内链接）/ **API / JSON**）；
  - 选 API ⇒ `extract.mode=json` 并**显示**「JSON 记录路径」输入（`extract.item_path`），
    其余来源 ⇒ 回到 `html` 且该行隐藏（§4.5 渐进呈现）；
  - **用户显式选过的来源不再被「开始」的草稿覆盖**（`_source_kind_overridden`）；
  - **未知来源类型原样加入下拉**（插件可注册自己的 kind，不能静默改写）；
  - 序列化器按**模式**翻译字段键：JSON 模式写 `path`（`json_field_values` 只认 `path`/`paths`），
    HTML 模式仍写 `selector` —— 用户在表单"选择器"列填的 JSONPath 因此能真正生效；
  - `extract.mode` **刻意不在每次保存时写**：只在用户切换来源时改，否则会把"打开来的 JSON 配置"
    在保存时悄悄改成 html。
  证据：`test_form_created_api_task_runs_end_to_end`（**空白表单 → 保存 → 运行 → `records.csv`
  等于真值**）、`test_form_can_choose_api_source_and_record_path`（切换语义 / 记录路径行可见性 /
  显式选择不被覆盖），以及 `test_form_authored_json_field_is_written_as_path` + HTML 反向护栏。
  **承重性核对**：让 `extract_mode()` 恒返回 `html` ⇒ 端到端用例失败（JSON 字段被写成 selector、
  取不到值）；让 `_on_source_kind_changed` 变空实现 ⇒ 控件用例失败。

  **仍未闭环（如实登记）**：
  - 选择器为空的字段（如"元素自身 href"这类 `selector=""` + `attr=href` 的规则）会被**跳过** ——
    GUI 的 `FieldDef` 要求选择器非空，这类规则目前只能靠 YAML 表达。
- **GUI 曾无法创建（author）`extract.item_selector`**（2026-09-13 实测 → **2026-09-14 已修**）：从零在表单里建不出"列表"任务。**现已闭环**——
  核实方式：逐条查 GUI 的输入路径 ——
  ① 表单（TaskCanvas）没有该控件；② 「启发式补全字段」只追加**通用**字段规则
  （`h1`/`a`/`time`/`.author`/`.description`，与目标页无关）；③ `visual_selector` 整个模块没有
  "容器 / 列表项"概念；④ 向导只有 `step3_fields.py`；⑤ 序列化器把 `item_selector`（与 `extract.mode`）
  当 **B 类透传字段**——只保留、不改写。
  实测产物（新任务 → 填网址与描述 → 「开始」→ 「启发式补全字段」→ 「保存草稿」）：
  `source.kind: crawl`、`max_pages: 30`、`extract.item_selector: ""`、字段为通用规则。
  （以上是修复前的实测产物。）

  **修复（2026-09-14）**：
  - 表单字段区新增「**列表项选择器**」输入口（含帮助条目 `fields.item_selector`、无障碍名与说明、
    占位符示例）；改动它会让**试跑闸门失效**（容器决定"一条记录"的边界，不允许拿旧试跑结果放行）；
  - `CrawlConfig` 配了一对 `item_selector()` / `set_item_selector()`，与 `extract_mode()` 同源，
    避免"读取在一处、写入在另一处"；
  - 锁定态（工作台占用中）与其它表单控件一起被禁用。

  **新增证据**：`tests/integration/test_gui_form_create_task.py` 增至 9 例，其中
  `test_form_created_list_task_runs_end_to_end` 是**账本「经 GUI 表单创建任务」这条验收的正面证据**：
  从**空白表单**填网址 → 填列表项选择器 → 加两条字段规则 → 保存 → 真实运行 →
  `records.csv` 三条真值逐条对上。**承重性核对**：让 `set_item_selector` 失效（表单值到不了配置）后
  该用例失败（退化成整页单条记录）——证明容器确实在起作用。

  ⚠️ 仍属**有意不做**的一项（不是遗漏）：`http.allow_private_network` 也**没有**表单控件 ——
  它是安全默认（禁止访问本机/内网/保留地址），测试与内网使用需显式在配置里声明。

  **证据**：`tests/integration/test_gui_form_create_task.py`（7 例）：
  - 表单创建 → 保存 的往返（网址 / 描述 / 来源类型 / 页数预算 / 字段都落盘且对得上）；
  - 「开始」的意图映射（单页 → `static_html`+1 页；采集栏目 → `crawl`+30 页）；
  - **透传保留**：打开带 `item_selector` 的列表配置、或在表单里改一改再保存，
    `item_selector` / `mode` / `item_path` 都**不被清空**（把既有修复钉在"表单保存"这条路径上，
    此前只验过"加载"）；
  - **限制钉住**：纯表单创建的配置里 `item_selector` 为空 —— 修好后请改断言并同步本条目。

  **承重性核对**：注入"表单→配置同步被破坏"后往返用例失败；注入"透传读不到"后保留用例失败。

  **顺带发现的测试卫生问题（已修）**：GUI 委托出错时走 `QMessageBox.critical`（模态），
  离屏测试里会**永久挂起**（本轮真的踩到：一个写坏的 YAML 让用例挂死，靠 `faulthandler` 才定位）。
  新增文件里已用 autouse fixture 把模态框换成记录器，并加"守卫自身有效"的自检用例。
- **自动配置曾丢「链接外字段」，且会拿侧边栏当列表**（2026-09-13 实测 → 同日**已修**）。
  两项都来自真实场景报告，均已复现并修掉：

  ① **价格落在 `<a>` 之外时取不到**。`detect_repeating_patterns` 的"直接子元素含标题"
  加分（+0.15）让卡片内的 `<a>`（含 img + h2）压过外层 `<li>`（含 a + span.price），
  而 `infer_fields` **无条件取 `patterns[0]`** ⇒ 容器选成 `<a>`、字段只剩「标题 + 图片地址」。
  **已修**：`infer_fields` 在前 3 个候选之间按「能推断出多少可用字段」择优。
  实测（woocommerce 形态）修复后 `item_selector = body > ul.products > li.product`，
  字段 = 价格 / 标题 / 图片地址，`价格.examples[0] == "$10.00"`。

  ② **页面没有业务列表时交出的却是侧边栏**。只含 4 个链接的侧边栏页面会产出
  `item_selector = body > aside.sidebar > a` 并"试跑通过"（`items=4 / records=4`）——
  旧门槛只看记录数（`items <= 2` 时降到 1），**会放行**。
  **已修**：`_check_verified` 增加两道门禁（仅作用于 HTML 且已写入 `item_selector`）：
  容器落在页面框架（`aside`/`nav`/`footer`）或列表项 < 3 个 ⇒ 明确报「未识别出列表」；
  单页/单对象模式（无 `item_selector`）不适用。

  ③ 顺带修掉：旧的 `leaf` 签名把 h1 + span + p 并成一组（`count=3`），使详情页被当成"列表"、
  容器落到 `body > article > h1`，且因"有模式"而走不到本该走的单页兜底。现按标签区分签名。

  证据：`tests/unit/extraction/test_analyze_to_config.py` 新增 5 例（含两条反向守卫：3 条小列表
  不被误杀、异质叶子不构成重复模式）。**承重性核对**：注入旧 `infer_fields` 后"链接外价格"
  用例失败；侧边栏配置在旧门槛下确实 `records=4` 会被放行 ⇒ 新门禁必要。

  ④ **仍未闭环（本轮新观察）**：详情页改走单页模式后**字段偏少** —— `_infer_single_page_fields`
  要求文本长度 ≥5，于是"商品名"(3 字符) / "99" 这类短字段被滤掉。属既有启发式，本轮未动。
- **无头浏览器：事件驱动型懒加载尚未验证（有实测依据，不掩盖）**（2026-09-13）。
  实测（Windows 无头 Chromium，同一页面、同一动作序列重复跑）：`window.scrollTo` 能改变滚动位置，
  但 **scroll 事件的派发不稳定**（同一参数多次运行，计数时有时无）；**IntersectionObserver 亦不稳定**
  （出现整轮 0 回调）。根因是**无头下渲染帧预算被压得很低**：纯空闲 800ms 连续 5 次启动测得的
  rAF 帧数为 `[1, 7, 12, 7, 29]`；关掉后台/遮挡抑制后为 `[13, 43, 14, 38, 11]`（更稳也更高）。
  ⇒ 结论与处置：
  ① 滚动验收样例改用**轮询滚动位置**作为触发条件（确定可重复，已连跑多次稳定），
  因此账本里"滚动"一行证明的是**滚动位置驱动的加载**；
  ② **"IO / scroll 事件驱动的懒加载"已于 2026-09-16 定论**（`W3.3`）：
     **平台能力＝已验收**：独立测量（每次滚动后回顶，保证视口真实位移）下，
     IntersectionObserver 与 scroll 事件**各 6/6 达标**（真实滚轮 `mouse.wheel` 与合成
     `window.scrollTo` 两种刺激都试过）。2026-09-13 那条"事件派发不稳定"的怀疑
     **根因是样例缺陷**：哨兵一次性滚到底后不再"离开再进入"视口，IO 自然只回调 1~2 次。
     **产品动作＝已知限制**：`scroll_bottom` 只"滚到当前底部"，位置不再变化时不产生事件 ——
     同一夹具下走产品动作时 scroll 事件只拿到首屏 4 条、IO 8 条，而**位置轮询 12 条稳定**。
     ⇒ 验收样例**继续以位置轮询为准**（确定性），事件驱动那两条断言**暂不入套件**
     （现在写只会得到随机红）；**后续项**：让 `scroll_bottom` 校验"位置确实变了"再决定是否回顶重试，
     那时把断言补回。证据与被收敛的用例见
     `tests/integration/browser/test_event_driven_lazy_loading.py`（docstring 内含测量表）；
     帧预算实测（800ms rAF 回调）`[0, 0, 14, 50, 49]` —— **仍会出现整轮 0 帧**，
     这也是"不把验收建立在事件上"的直接理由。
  ③ 浏览器启动参数（TLS 规则 + 无头保真参数）已收口到 `fetching/browser_launch.py` 单处，
  并由 `tests/unit/fetching/test_browser_launch_args.py` 锁住（含"两条路径都必须走它"的防再漂移守卫）。
  **如实声明**：我一度以为保真参数修好了滚动加载，随后用重复测量**否证了自己**（同一参数下条目数仍在
  8~12 波动、"无参数"也并非必然失败），故只在文档中记为"降低被降频的概率"，不记为缺陷修复。
- **「取消」曾与「失败」合并为「错误」**（2026-09-13 实测 → 同日**已修**）。
  `gui/runner/worker_task_runner.py::_poll` 的终态分支里，`partial_success` 与
  `succeeded` 各有专门分支，而 **`cancelled` 与 `failed` 共用一个 `else`**：

  ```python
  else:                                   # ← cancelled 落这里
      self._set_state("error")
      self.task_finished.emit(self._current_task_id, 1)
  ```

  后果：用户**主动点「停止」**，界面显示的是"错误"、任务被判失败（退出码 1），
  且没有任何"已取消"的提示。这与项目既有的"三态统一 / 错误不伪装"原则不符
  （取消是用户意图的结果，不是错误）。
  本轮实测证据（慢站点 9 页，运行中停止）：后端 `status=cancelled`、`pending=6`、
  `processed=3` —— **后端语义正确**，缺陷只在 GUI 呈现层。
  修法方向：为 `cancelled` 增加独立终态（如"已取消"），与 `failed` 分开；
  退出码语义需一并明确（取消 ≠ 成功，也 ≠ 失败）。**属交互语义变更，须先定调**，
  故本轮只在用例中按现状断言并标注（`test_gui_worker_local_task.py` 用例 5）。

  **已修（2026-09-13，用户拍板方案 A）**：
  - `_poll` 增加 `cancelled` 独立分支 → 终态用核心状态机的**规范名** `cancelled`
    （`core/run_state.py` 的 `RUN_STATES`／`TERMINAL_RUN_STATES` 均含它，
    `running→cancelled` 也是合法转换），不再与 `failed` 合并；
  - **退出码保持 1**：与 CLI 一致（`commands/run_task.py`：`{failed, cancelled} → exit_code 1`），
    不自创别的码值；同时补一条用户可见提示「任务已取消，已完成的成果已保留」；
  - 状态词表补齐四处：状态栏文案「已取消」、状态指示器颜色（中性色，与 error 红区分）与
    tooltip、任务历史图标 `⏹`；**取消时也把已采到的结果载入结果页**（"已有有效输出受保护"），
    但自动打开目录/自动导出/提示音仍只属于正常完成。
  - 回归护栏：`test_worker_task_runner.py` 新增终态映射用例（cancelled → 独立终态 + 退出码 1 +
    有提示），`test_gui_worker_local_task.py` 用例 5 改断言独立终态。
  - 遗留（设计层，未做）：GUI 用 UI 本地名（`finished`/`error`）而核心用规范名
    （`succeeded`/`failed`），本次只把 `cancelled` 对齐规范名，词表统一属后续重构。
- **等宽字体链不含中文字形**（2026-09-13 读代码发现，**待实机确认后再定是否修**）。
  `gui/design_system.py`：UI 链 `PingFang SC → Microsoft YaHei → Noto Sans CJK SC → Segoe UI`
  有跨平台中文回退 ✓；但
  `FONT_FAMILY_MONO = "JetBrains Mono, Consolas, Cascadia Code, Menlo, monospace"`
  **不含任何带中文字形的字体**。GUI 不随包自带字体 ⇒ 中文出现在**等宽控件**
  （YAML 编辑器、日志控制台、转换工具日志）时只能靠 Qt / fontconfig 的逐字隐式回退，
  **部分 Linux 上可能显示为方块**。属**产品级隐患**（不是资产问题），且离屏测试根本覆盖不到
  ⇒ **须由人工走查在非 Windows 平台上确认**（截一张中文出现在等宽控件的图），
  确认后再决定是否把 `Noto Sans Mono CJK SC` 之类加进该链。
- **`omnicrawler pdf` 曾转发不了 `--config`**（2026-09-13 实测 → 同日**已修** `bbc6728`）。
  `cli/_parsers/pdf.py` 用 `argparse.REMAINDER` 原样转发，而 REMAINDER **不捕获以选项开头的
  参数**；pdfx 的 `--config` 又必须位于子命令**之前** ⇒ 三种写法都失败，**自定义 PDF 项目从顶层
  完全不可达**（只能用内置模板；而 console script `pdfx` 在便携产物里未必存在）。
  **已修**：把 `pdf` 纳入 `_main.py` 里**已有的执行路径预切分**（原只给 `pdf-process`/`pdf-extract`），
  **只切执行路径、不碰发现路径** —— `pdf` 仍是注册子命令，`--help` 与 CLI 文档契约照旧。
  刻意**不做**"带顶层全局选项也能用"的形态（那要复制一套 pdfx 选项知识＝第二个真源）。
  证据：`tests/unit/cli/test_pdf_forwarding.py`（3 条，锁可用形态 + 默认行为 + 仍是注册子命令）。
  **补充（2026-09-13，同日）**：`--config` **写在子命令之后**（`omnicrawler pdf doctor --config X`）
  仍然不可用 —— `argparse` 的父解析器选项必须在子命令之前。原先记为"刻意不做（要复制 pdfx 选项知识
  ＝第二个真源）"，**这个理由只对"顶层选项提升"成立**；正确的解法更简单：pdfx 本身就在本仓库，
  于是只在 `pdfx/cli.py` 加**一条搬迁规则** `_hoist_parent_options()`（把 `--config` 从子命令后提到前面），
  **不涉及任何选项定义**，因此不构成第二真源。三种位置（前/后/`=` 形式）现均可用，
  `--help` 与 console script `pdfx` 不受影响。
  证据：`tests/unit/pdf/test_pdfx_config_position.py`（纯函数 8 例 + 解析器 3 形态 + 默认模板不变
  + **子进程端到端**两条位置都真的读到配置）。
- **OCR 文本的汉字间空格会原样进入 `text` 字段值**（2026-09-13 实测 → 同日**提供显式开关**）。
  `chi_sim` 会在汉字之间插空格（`示例服务合同` → `示例  服务  合同`），而取值模式
  `(?P<value>[^\n]+)` 会把整行余下内容都收进来，于是值里保留空格。
  **已按方案 C 处理（不改默认产出，只提供显式开关）**：字段级 `collapse_whitespace: true`
  （默认关）——
  - 归一规则是**语言感知**的两步：① 连续空白折叠成单个空格；② 删掉**相邻汉字之间**的那一个空格。
    只做①会得到 `示例 服务 合同`，仍与真值不一致；只删空格会破坏英文
    （`Sample Service Contract`）。故②只作用于"汉字紧邻汉字"，英文/数字之间的空白保留。
  - 配在数值/日期类字段上**直接报错**（不允许静默失效，沿用 `pdfx/config.py` 的 D26/D27 原则）；
    原始值仍在 `…_原始值` 与原文证据里，归一不改证据。
  - 证据：`tests/unit/pdf/test_collapse_whitespace_option.py`（默认关/开启/语言感知/非法组合报错）
    ＋端到端 `test_pdf_ocr_review_workflow.py::test_ocr_whitespace_collapse_is_opt_in`
    （图片版样本开启后取值与真值**逐字符相等**）；内置模板已加用法注释。
  - **OCR 千分位误读：从"只能靠复核兜住"改为"结构判定后恢复或交复核"**（2026-09-13）。
    原记述不完整：OCR 会把千分位逗号读成小数点（`12,345.67` → `12.345.67`），而旧 `_number()`
    的正则 `[-+]?\d[\d,，]*(?:\.\d+)?` **只匹配到 `12.345`** —— 交付出去的是一个**看起来完全合理、
    却错误**的值，且 `金额_字段置信度` 仍为 **0.98**（零警示信号）。同一函数还会把 `1.234.567` 截成
    `1.234`、欧式 `1.234,56` 截成 `1.234`。这违反本项目"异常不得伪装成功／产出不许静默失效"的既有原则。
    **已修**：`_parse_numeric_token()` 改为**先判结构** —— 分隔符 ≥ 2 时只接受两种可判定读法
    （① 全部当千分位且分组规范；② 最右为小数点、其余为千分位且整数部分分组规范），两者都成立取 ①，
    **都不成立则返回 None（交人工复核，不猜）**；全角 `．`/`。`/`，` 先归一。
    证据：`tests/unit/pdf/test_numeric_token_structure.py`（18 例：恢复 8 种形态、不可判定 3 种、
    "永不返回截断前缀"的防回归契约、单分隔符读法不变、number/integer/percent 同规则）。
    **端到端升级**：`test_pdf_ocr_review_workflow.py` 的金额断言由"只断言非空"改为**与真值逐字符相等**
    （实测 `金额=12345.67`、`金额_原始值=12.345.67 元` ⇒ 恢复可审计）。
    **承重性核对**：把旧抓取行为注入回去后，同一用例**失败**（`金额` 变 `12.345`）—— 证明该断言真的在测这件事。
- **INV-008 曾「声明引用不存在的证据」**（2026-09-13 实测 → 同日**已按方案 B 修正**）。
  `docs/SECURITY_AND_COMPLIANCE.md` 把该不变量标注为由 `updates.confirm_missing_runs`
  及变化追踪实现、证据为 `test_v110_features.py` 的连续缺失测试。实测：
  ① 该测试文件**不存在**（全仓无 `test_v110_features.py`，INV-004 也引用同一不存在的文件）；
  ② `updates.confirm_missing_runs` 在 `src` 中**只有默认值与校验**，**没有任何消费点**；
  ③ 全仓无任何测试涉及「连续缺失」/`possibly_removed`。
  即：这是一条**文档声称已实现、但代码与证据都不支撑**的不变量。

  **已修（2026-09-13，用户拍板方案 B：修正声明）**：
  - `docs/SECURITY_AND_COMPLIANCE.md` 的 INV-008 按**实际实现**重写为
    「记录消失只在**完整运行之间**确认（后一次运行未完成则降级为待确认）」，
    实现列改为 `review.run_compare.compare_runs` 的 `removed` / `possibly_removed`；
  - 新增 `tests/integration/test_run_compare_deletions.py` 作为证据（3 条：完整运行间确认删除、
    未完成降级为 possibly_removed、同名不同身份不合并）。**对照实验**：同一场景仅"后一次
    是否收尾"之差 → `removed` 0→1、`possibly_removed` 1→0，证明断言真的在测完备性闸门；
  - 同一份文档里 **INV-004 也引用了同一个不存在的文件**，一并修正：其契约由
    `reset_record_stage` 的 docstring 明确（只清派生输出、保留 responses 与原始归档），
    新增 `tests/unit/state/test_reprocess_preserves_raw_evidence.py` 作为直接证据
    （并做注入实验：破坏"保留原始证据"后该用例精确失败），配套 `test_replay.py` /
    `test_pipeline.py`；
  - `updates.confirm_missing_runs` **保留以兼容既有配置**，但在 `core/config.py` 注明
    "预留未实现、无任何消费点"，不再声称它实现任何不变量。
- **变更检测的两条路径语义不同，且主路径不产出「删除」**（2026-09-13 实测）。
  `state.track_semantic_changes()` 只遍历**本次**记录，`after` 永远非 None ⇒
  **永远产不出 `removed`**，只能报 `added` / `modified`；「记录消失」由
  `review.run_compare.compare_runs()` 在两次 run 之间比对时给出（含 `possibly_removed`，
  依据是 after run 是否完整，而非"连续 N 次缺失"）。差异**导出**存在但不在运行时 `outputs` 段：
  需用产品自身的 `omnicrawler compare-runs -c <cfg> <before> <after> -o <file>` 写出差异文件
  （与 GUI 菜单「对比两次运行」同源函数）；2026-09-13 已把该导出路径纳入端到端用例。
  （更正：本条此前写作"差异导出产物当前未见到"，不准确。）
- **首次同步把初始记录全部记为 `added`**（2026-09-13 观测 → 同日按**方案 C** 落地）。
  原状：无历史可比时 `semantic_changes = {"added": N}`，下游无法区分"首轮基线"与"真的变化"。
  按用户裁决（方案 C）**不再改变记录本身，而是把事实标出来并把决定权交给消费方**：
  - 数据层：`semantic_changes.baseline` 列（+ 旧库自动迁移），首轮的新增记为**基线**；
    `SemanticChange.baseline` 同时进入每条记录的 `evidence._semantic_change`；
  - 报告层：`semantic_changes`（完整事实，**不变**）之外新增 `semantic_changes_baseline`
    （其中属基线的部分）；报表/看板读前者不受影响；
  - 消费层：新增**显式入口** `quality_report.notifiable_changes(report)` —— 要提示就调它
    （已排除基线），不要提示就用完整事实。**数据层不替用户判断"是否打扰"**。
  - 证据：`tests/unit/quality/test_semantic_change_baseline.py`（首轮标基线且不可提示、
    次轮不标且可提示、旧库迁移）＋ GUI 端到端用例（首轮 baseline 有值、次轮为空）。
  - ★ 实测教训（已写进代码注释与回归护栏）：**"首轮"必须按 `project_name` 判定，不能按
    `config_path`** —— GUI 每次运行都会把配置另存为 `configs/<项目名>_<时间戳>.yaml`，
    用 config_path 判定会把第二轮误当首轮（该错误被端到端用例当场抓到）。
- **GUI 路径的闭环证据已扩到 4 条用例**（2026-09-13）。
  `tests/integration/test_gui_worker_local_task.py`：真实 `WorkerTaskRunner` + 真实
  `LocalWorkerBackend` 子进程（断言 pid ≠ 当前进程、且事后退出无残留）→ 本地固定 HTTP 站点 →
  ① 单页列表：恰好 3 条、字段真值、`source_url` 正确、JSONL+CSV；
  ② 列表→详情两级：4 页 6 条、来源覆盖 4 个 URL、**XLSX 可重新打开且内容对得上真值**；
  ③ API 游标分页：3 页各一条、游标链恰好一遍并在末页停止、二次同步只交付变化那条；
  ④ 定期重跑变更检测（见上方「定期采集」一行）。
  已做两次**独立性核对**（都不是假通过）：一是模拟修复前行为时用例① 退化为 1 条并失败；
  二是把用例② 的 `source_kind` 换成 `static_html`（不跟随链接）时只剩 3 条 —— 说明 6 条
  只有真正走两级 `crawl` 才拿得到。
  仍未覆盖：便携产物内复跑（按约定留 CI / 受控环境）。
  参考：`tests/integration/sdk/test_execution_backend.py` 仍只验控制面，
  `test_worker_task_runner.py` 仍用假 backend。
- **GUI 真实入口已补证据**（2026-09-13，`tests/integration/test_gui_entry_run.py`）。
  走用户真正点的那条路：工具栏「运行」按钮 → `MainWindow._request_run` → 配置校验与
  「试跑一致」闸门 → `RunController.run_task` → worker 子进程 → 状态栏「已完成」→
  结果页自动载入本次 `records.csv`（行数到位、内容等于真值）。用 `QPushButton.click()`
  触发**真实信号链**，不伪造槽函数调用；并加反向护栏断言「未试跑时运行按钮禁用」，
  以证明启动确实通过了试跑闸门。
  仍未覆盖：真实鼠标点击（这里用 `click()` 触发信号）、试跑本身（由
  `test_first_task_journey.py` 覆盖）、便携产物内运行。
- 2026-09-13 已修并登记（两条同源：GUI 模型未建模的键与核心契约脱节，提交 `a5bdfbd`、`4a546ec`）：
  ① **配置往返静默降级**：`save_yaml` 把 `extract.mode` 写死 `"html"`、`extract.item_selector`
  写死 `""`、`http.auto_browser_fallback` 写死 `True`，而 `_deep_overlay` 让 root 胜出 ⇒
  在 GUI 里打开一个可用的列表/JSON 配置再运行会被降级。触发面是**每次运行**。
  ② **合法 JSON 配置无法从 GUI 启动**：JSON 字段契约是 `path`/`paths`，而 GUI 模型只有
  `selector`（`path` 属透传键）⇒ 模型里 selector 恒空，`FieldDef.validate()` 与
  `validate_selector_format()` 一律要求选择器非空 ⇒ `runner.start()` 直接返回 False。
  已引入 `CrawlConfig.extract_mode()` 作为"当前模式"的唯一来源，校验按模式区分，
  HTML 模式仍严格要求选择器（有反向护栏断言）。
- 2026-09-12真实场景报告的汇总口径是“产出大于0”，不能证明满足原始字段和范围需求。
- apex/www 重定向场景曾出现250条变500条；当前已实现“成功重定向的精确最终URL在本周期
  登记为已完成别名”，并以真实本地302、恢复和新周期回归验证。公网原场景复测仍待执行。
- 单对象JSON API已能生成REST配置；自动配置门禁复用正式运行的JSONPath和字段候选
  规则。本地固定游标API已证明首次完整交付、末页停止和二次仅交付变化记录；GUI创建
  与便携产物路径仍待验证。
- 首任务自动回归不经真实点击与任务子进程；人工走查记录仍为空。
- 三平台打包脚本已改为按 edition 执行 `uv sync --locked`，包内及平台级SBOM会逐包核对
  `uv.lock` 版本。当前只完成静态契约与工具反例验证，三平台真实构建CI仍待运行。

## 证据更新规则

每条更新记录提交号、系统与依赖、输入/配置/产物哈希、执行入口、通过项、失败项、跳过原因和
证据路径。公网不可达只能记为“该环境该时刻不可达”；不能据此断言站点退役或域名不存在。
