# 代表性任务验收账本

> 适用版本：0.12.0 · 建立日期：2026-09-12 · 基准提交：`13ea424` · 维护状态：现行

本账本记录“当前版本真实证明了什么”。历史报告不自动等于当前版本通过；没有当前证据的
能力标为未知。每次执行必须使用独立 workspace 与 data_dir，恢复测试只复用本任务状态。

| 工作流 | 固定输入与正确答案 | 当前自动证据 | 当前状态 | 下一项验收 |
|---|---|---|---|---|
| 静态列表→详情→结构化结果 | `quality_benchmark.py` 的 `list-three-details`，6条记录、title/price、逐条来源路径 | 本地HTTP真实流水线；精确评分含漏采、错值、错来源、额外及重复记录；**2026-09-13 新增 GUI 闭环两条**：`test_gui_worker_local_task.py` 经真实 `WorkerTaskRunner` + 真实 worker 子进程（pid≠当前进程、事后退出无残留）跑本地站点——① 单页列表恰好3条真值+CSV；② **列表→详情两级 4 页 6 条**、来源覆盖 4 个 URL、**XLSX 可重新打开** | 已验证（JSONL/CSV/XLSX 核心链，含 GUI 子进程、两级抓取与**真实运行按钮入口**） | 便携产物内复跑（按约定留 CI / 受控环境） |
| 动态页面→分页/滚动→去重 | 今日真实场景报告含 quotes/js；输入依赖公网，无冻结真值快照 | 最近单元回归覆盖浏览器发现链接和子请求继承渲染 | 部分验证 | 固定动态样例核对首末页、总数、重复率与浏览器回收 |
| API→游标分页→增量 | 本地HTTP固定3页：首次交付ID 1/2/3；二次仅末页值变化，应只交付ID 3新值 | 自动分析按正式JSONPath试跑；真实流水线核对游标替换、末页停止、来源URL及二次同步；恢复保留未完成游标；**2026-09-13 新增 GUI 路径**：同场景经真实 GUI 运行器 + worker 子进程跑通（3 条含游标来源、游标链恰好一遍、二次只交付变化那条） | 已验证（JSONL核心链，含 GUI 运行路径） | 经 GUI 表单**创建**任务；便携产物内复跑 |
| 附件→PDF/OCR→复核（**自动部分**） | 程序自造样本（零第三方内容）：数字版=reportlab 中英文+表格；图片版=PIL 渲染的**无文字层** PDF（用来逼出 OCR）。真值：`HT-2026-0001` / `示例服务合同` / `12,345.67 元` | **2026-09-13 新增端到端**（`tests/integration/pdf/test_pdf_ocr_review_workflow.py`）：① 数字版取到中英文与表格（还原为 Markdown 表）、金额标准化为「元」、字段带**页码 + 原文证据**、校验 `valid`；② 图片版 `parse_method=ocr`、`ocr_status=done`、置信度 > 0.5（用**项目内置 tesseract** `.runtime/tesseract/`），OCR 文本还原出编号与名称，宽容模式抽取 `valid`；③ 故意用严格模式制造失败 → `invalid`/`needs_review` + 校验信息说明缺哪些必填字段 → 复核队列 → 人工补齐并确认 → `apply-review` 写入 `human_review` 值 → **再导出可见** | 已验证（自动部分） | — |
| 附件→PDF/OCR→复核（**人工部分**） | 同上样本与真值 | 无自动证据（按定义不可自动） | 未知（须人工走查） | 真人在 GUI 中复核的视觉与理解成本；以及"人工判断本身是否正确" |
| 定期采集→变更检测→差异导出 | 本地固定两版输入：v1=甲乙丙；v2=甲改价、乙不变、丙移除、丁新增 | **2026-09-13 新增端到端**：经真实 GUI 运行器 + worker 子进程连跑三次——v2 报 `modified=1`、`added=1`（中文键身份生效）；「删除」经 `run_compare` 检出 `removed=1`、`possibly_removed=0`；**差异导出**经产品自身 `compare-runs -o <file>` 写出文件且计数一致；第三次内容不变 → **零差异（无假差异）** | 已验证（变动识别 + 删除 + 差异导出 + 无假差异；删除走 run_compare 而非主路径） | `updates.confirm_missing_runs` 的连续确认（见缺口）；首次同步是否应报变更（待决策） |
| 长任务→中断→恢复 | 故障注入与取消恢复用例已有；**2026-09-13 新增 GUI 路径**：慢站点（列表 + 8 详情），运行中经 GUI 停止 | 真实 GUI 运行器 + worker 子进程：**取消时服务端仅命中 3/9 页（对照实验：不取消 = 9/9）**、后端 `status=cancelled` 且 `pending>0`（证明确为中途停止）；停止后子进程回收无残留；重启后按标题去重**无重复**、两次运行合起来**覆盖全部页面** | 已验证（GUI 停止真实有效 + 进程回收 + 重启不重复不遗漏）；「resume 只交付剩余」由 ApplicationService 级用例覆盖 | 便携产物内复跑 |

## 当前已知缺口

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
- **`omnicrawler pdf` 转发层传不了 `--config`**（2026-09-13 实测，**待决策**）。
  `cli/_parsers/pdf.py` 用 `argparse.REMAINDER` 原样转发给 PDF 子系统，但 REMAINDER
  **不捕获以选项开头的参数**，而 pdfx 的 `--config` 又必须位于子命令**之前**。三种写法都失败：
  `pdf --config X doctor`（顶层报 unrecognized arguments）、`pdf doctor --config X`
  与 `pdf -- --config X doctor`（pdfx 报错）。**结果：`omnicrawler pdf` 只能用内置模板**；
  自定义项目只能走 console script `pdfx`（`pyproject.toml` 的 `pdfx = omnicrawler.pdfx.cli:main`）
  或进程内调用。本轮端到端用例用的是**进程内调用**（与顶层 `pdf` 子命令内部同一条路径）。
  修法方向：在转发层把全局选项（当前仅 `--config`）提到子命令之前，或在 `_main` 里对 `pdf`
  做受控预切分。**属 CLI 行为变更，须先定调**（这份文件自己强调过"不要出现第二个真源"）。
- **OCR 文本的汉字间空格会原样进入 `text` 字段值**（2026-09-13 实测）。
  `chi_sim` 会在汉字之间插空格（`示例服务合同` → `示例  服务  合同`），而取值模式
  `(?P<value>[^\n]+)` 会把整行余下内容都收进来，于是值里保留空格。内容没错，
  但比较/下游匹配需要容忍空白；**收紧取值模式（或对 text 字段折叠连续空白）属配置/产品决策**。
  同一批 OCR 还会把全角冒号输出成半角（`合同编号：` → `合同编号:`）、把千分位逗号读成小数点
  （`12,345.67` → `12.345.67`）—— 这些都是复核环节存在的理由，用例已按"内容取到"而非
  "逐字符相等"来断言。
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
