# 代表性任务验收账本

> 适用版本：0.12.0 · 建立日期：2026-09-12 · 基准提交：`13ea424` · 维护状态：现行

本账本记录“当前版本真实证明了什么”。历史报告不自动等于当前版本通过；没有当前证据的
能力标为未知。每次执行必须使用独立 workspace 与 data_dir，恢复测试只复用本任务状态。

| 工作流 | 固定输入与正确答案 | 当前自动证据 | 当前状态 | 下一项验收 |
|---|---|---|---|---|
| 静态列表→详情→结构化结果 | `quality_benchmark.py` 的 `list-three-details`，6条记录、title/price、逐条来源路径 | 本地HTTP真实流水线；精确评分含漏采、错值、错来源、额外及重复记录；**2026-09-13 新增 GUI 闭环两条**：`test_gui_worker_local_task.py` 经真实 `WorkerTaskRunner` + 真实 worker 子进程（pid≠当前进程、事后退出无残留）跑本地站点——① 单页列表恰好3条真值+CSV；② **列表→详情两级 4 页 6 条**、来源覆盖 4 个 URL、**XLSX 可重新打开** | 已验证（JSONL/CSV/XLSX 核心链，含 GUI 子进程与两级抓取） | 真实按钮点击；便携产物内复跑 |
| 动态页面→分页/滚动→去重 | 今日真实场景报告含 quotes/js；输入依赖公网，无冻结真值快照 | 最近单元回归覆盖浏览器发现链接和子请求继承渲染 | 部分验证 | 固定动态样例核对首末页、总数、重复率与浏览器回收 |
| API→游标分页→增量 | 本地HTTP固定3页：首次交付ID 1/2/3；二次仅末页值变化，应只交付ID 3新值 | 自动分析按正式JSONPath试跑；真实流水线核对游标替换、末页停止、来源URL及二次同步；恢复保留未完成游标；**2026-09-13 新增 GUI 路径**：同场景经真实 GUI 运行器 + worker 子进程跑通（3 条含游标来源、游标链恰好一遍、二次只交付变化那条） | 已验证（JSONL核心链，含 GUI 运行路径） | 经 GUI 表单**创建**任务；便携产物内复跑 |
| 附件→PDF/OCR→人工复核 | 尚无贯穿复核修改与最终输出的固定任务 | PDF/OCR专项测试存在 | 未知 | 固定附件、字段位置、不确定性、人工修改及导出 |
| 定期采集→变更检测→差异导出 | 本地固定两版输入：v1=甲乙丙；v2=甲改价、乙不变、丙移除、丁新增 | **2026-09-13 新增端到端**：经真实 GUI 运行器 + worker 子进程连跑三次——v2 报 `modified=1`、`added=1`（中文键身份生效）；「删除」经 `run_compare` 检出 `removed=1`、`possibly_removed=0`；**差异导出**经产品自身 `compare-runs -o <file>` 写出文件且计数一致；第三次内容不变 → **零差异（无假差异）** | 已验证（变动识别 + 删除 + 差异导出 + 无假差异；删除走 run_compare 而非主路径） | `updates.confirm_missing_runs` 的连续确认（见缺口）；首次同步是否应报变更（待决策） |
| 长任务→中断→恢复 | 故障注入与取消恢复用例已有；**2026-09-13 新增 GUI 路径**：慢站点（列表 + 8 详情），运行中经 GUI 停止 | 真实 GUI 运行器 + worker 子进程：**取消时服务端仅命中 3/9 页（对照实验：不取消 = 9/9）**、后端 `status=cancelled` 且 `pending>0`（证明确为中途停止）；停止后子进程回收无残留；重启后按标题去重**无重复**、两次运行合起来**覆盖全部页面** | 已验证（GUI 停止真实有效 + 进程回收 + 重启不重复不遗漏）；「resume 只交付剩余」由 ApplicationService 级用例覆盖 | 便携产物内复跑 |

## 当前已知缺口

- **「取消」被 GUI 呈现为「错误」**（2026-09-13 实测，**待决策**）。
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
- **INV-008「删除需连续确认」缺实现与证据**（2026-09-13 实测登记，**待决策**）。
  `docs/SECURITY_AND_COMPLIANCE.md` 把该不变量标注为由 `updates.confirm_missing_runs`
  及变化追踪实现、证据为 `test_v110_features.py` 的连续缺失测试。实测：
  ① 该测试文件**不存在**（全仓无 `test_v110_features.py`，INV-004 也引用同一不存在的文件）；
  ② `updates.confirm_missing_runs` 在 `src` 中**只有默认值与校验**，**没有任何消费点**；
  ③ 全仓无任何测试涉及「连续缺失」/`possibly_removed`。
  即：这是一条**文档声称已实现、但代码与证据都不支撑**的不变量。处置属产品/合规决定
  （补齐连续确认，或修正该不变量与引用），不宜由 AI 单方面改写合规声明。
- **变更检测的两条路径语义不同，且主路径不产出「删除」**（2026-09-13 实测）。
  `state.track_semantic_changes()` 只遍历**本次**记录，`after` 永远非 None ⇒
  **永远产不出 `removed`**，只能报 `added` / `modified`；「记录消失」由
  `review.run_compare.compare_runs()` 在两次 run 之间比对时给出（含 `possibly_removed`，
  依据是 after run 是否完整，而非"连续 N 次缺失"）。差异**导出**存在但不在运行时 `outputs` 段：
  需用产品自身的 `omnicrawler compare-runs -c <cfg> <before> <after> -o <file>` 写出差异文件
  （与 GUI 菜单「对比两次运行」同源函数）；2026-09-13 已把该导出路径纳入端到端用例。
  （更正：本条此前写作"差异导出产物当前未见到"，不准确。）
- **首次同步把初始记录全部记为 `added`**（2026-09-13 观测）。无历史可比时的当前语义；
  是否需要"首次不报变更"（把首轮当作基线）属产品语义决定，已在本轮测试中按现状断言。
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
  仍未覆盖：真实按钮点击（用例直接调用按钮所调用的同一函数）、便携产物内复跑。
  参考：`tests/integration/sdk/test_execution_backend.py` 仍只验控制面，
  `test_worker_task_runner.py` 仍用假 backend。
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
