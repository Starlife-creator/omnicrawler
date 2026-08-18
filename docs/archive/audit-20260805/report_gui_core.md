# 审查报告: gui 核心

审查日期: 2026-08-05  ·  审查人: opencode（资深 Python/Qt 审查员，仅审查，未修改任何文件）

## 汇总

### 审查范围（实际行数，与任务描述差异已在括号内标注）
| 文件 | 实际行数 | 说明 |
|---|---|---|
| `src\omnicrawler\gui\__init__.py` | 12 | 仅版本再导出 |
| `src\omnicrawler\gui\__main__.py` | 9 | 入口转发 |
| `src\omnicrawler\gui\main.py` | 2034 | 用户标注 1779，实际 2034 |
| `src\omnicrawler\gui\design_system.py` | 687 | 用户标注 569，实际 687 |
| `src\omnicrawler\gui\async_workers.py` | 405 | 用户标注 340，实际 405 |
| `src\omnicrawler\gui\home.py` | 413 | 用户标注 360，实际 413 |
| `src\omnicrawler\gui\settings.py` | 330 | 用户标注 255，实际 330 |
| `src\omnicrawler\gui\i18n.py` | 110 | 用户标注 78，实际 110 |
| `src\omnicrawler\gui\icon_registry.py` | 125 | 用户标注 102，实际 125 |
| `src\omnicrawler\gui\accessibility.py` | 35 | 用户标注 27，实际 35 |
| `src\omnicrawler\gui\motion_signal.py` | 60 | 用户标注 42，实际 60 |
| `src\omnicrawler\gui\shortcuts.py` | 98 | 用户标注 72，实际 98 |
| `src\omnicrawler\gui\help_center.py` | 101 | 用户标注 90，实际 101 |

### 语法检查
`python -m py_compile` 对全部 13 个文件执行：**全部通过，无语法错误**（PY_COMPILE_OK）。

### 问题计数（按严重级别）
- **critical: 2**
- **high: 8**
- **medium: 12**
- **ux: 8**
- **low: 10**
- 合计: 40

### 最重要问题速览（Top 10）
1. [critical] main.py:1972 — `--headless/--run` 模式在 `argparse` 构造时引用未定义的 `_()`，直接 NameError 崩溃。
2. [critical] main.py:1807 + async_workers.py:395 — 关闭窗口时 `cancel_all()` 可能留下仍在运行的 QThread（无 parent）被销毁 → "QThread destroyed while running" 崩溃。
3. [high] main.py:1811 — `_rebuild_wizard()` 把新向导 addWidget 到外层布局而非 splitter，重建后布局错乱。
4. [high] main.py:915 — 托盘菜单 `QMenu()` 无父对象且本地变量作用域结束后被 GC，托盘右键访问悬空指针。
5. [high] main.py:885-887 — 首页「最近任务」「结果与复核」导航到错误页面（YAML 编辑器 / 任务监控）。
6. [high] design_system.py:603 — `apply_font_strategy(scale=100)` 每次主题应用都覆盖 accessibility 缩放，界面缩放设置失效。
7. [high] home.py:352 — `_AIEnrichWorker` 无清理/停止机制，关闭窗口时线程仍在跑会被销毁。
8. [high] help_center.py:98 — `_copy_example()` 对未知帮助 ID 的兜底条目再调用 `get_help()` 抛 KeyError。
9. [high] async_workers.py:354 — `index_csv()` 向 `CsvIndexWorker` 传不存在的 `max_rows` 参数，一旦调用即 TypeError。
10. [high] main.py:1786 — 无托盘图标时任务运行中直接关窗静默 `stop()`，无任何确认/提示，存在任务中断丢失风险。

---

## 问题清单

### [critical] main.py:1972-1988 - `--headless/--run` 模式引用未定义 `_`，必然崩溃
- 现状：`_()` 只在第 60-170 行的 `if not _HEADLESS_MODE:` 块内 import（`from .i18n import _`）。`main()` 在模块级定义，第 1972 行构造 `argparse.ArgumentParser(description=_("..."))`，第 1977-1988 行的 help 文本同样使用 `_()`。
- 问题：`python -m omnicrawler.gui --run config.yaml` 或 `--headless` 时 `_HEADLESS_MODE=True`，跳过 import 块 → `main()` 一进入就 NameError: name '_' is not defined，无界面模式完全不可用（无 try/except 兜底，直接抛 traceback）。
- 建议：把 `from .i18n import _` 提到模块顶层（import 无副作用），或 headless 分支不经过 `argparse`（如手写解析），并补一个 headless 冒烟测试。

### [critical] main.py:1807 / async_workers.py:395-405 - 关闭窗口时销毁仍在运行的 QThread
- 现状：`closeEvent()` 对 `AsyncWorkerManager` 的线程调用 `cancel_all()` 后直接 `event.accept()`；`cancel_all()` 只 `requestInterruption()`+`wait(timeout_ms)`，超时未停的线程仍保留在 `_active_workers`。
- 问题：AsyncWorkerManager 创建的 worker `parent=None`，不属于 `_background_threads()`/`findChildren(QThread)`，不受延后关闭保护。若 CSV/JSONL/SQLite 正在处理大文件，窗口销毁时线程可能仍在运行 → Qt 报 "QThread: Destroyed while thread is still running" 崩溃。且 `wait(3000)` 逐线程串行执行，N 个 worker 最坏卡 GUI 3×N 秒。
- 建议：worker 全部以主窗口为 parent；`cancel_all` 用单次总预算等待 + 仍然在跑的线程标记并在其 `finished` 时再清理；closeEvent 走与其它后台线程一致的延后关闭流程（`_finish_deferred_close_if_safe`）。

---

### [high] main.py:1811-1837 - `_rebuild_wizard()` 布局重建错误
- 现状：向导页 `self._config_wizard` 位于 `wizard_splitter` 内（第 796 行）。重建时 `layout = self._wizard_widget.layout()`（外层 `wizard_layout`），`layout.removeWidget(old_wizard)` 因 old_wizard 不是该布局的直接子项而**无效**，随后 `layout.addWidget(self._config_wizard)` 把新向导直接加进外层布局。
- 问题：新向导被加到 splitter 下方成为独立全宽区块，splitter 内只剩旧向导（pending deleteLater）+ 信息面板。任何一次重建（快速草案、模板套用、编辑器同步、拖放加载、恢复草稿、历史加载）后，向导与右侧信息面板不再并排，布局明显错乱。
- 建议：重建时对新 `wizard_splitter` 重新 `addWidget`（并重设 `setSizes([600,200])`、stretchFactor），或把 splitter 保存为成员并 `splitter.insertWidget/replaceWidget`。

### [high] main.py:915 - 托盘菜单 QMenu 生命周期悬空
- 现状：`tray_menu = QMenu()` 无 parent；`self._tray_icon.setContextMenu(tray_menu)`（Qt 文档明确 setContextMenu **不接管所有权**）；方法结束后 Python 局部变量被 GC → C++ QMenu 被销毁。
- 问题：用户右键托盘图标时 Qt 操作已销毁的 QMenu → 崩溃或未定义行为。
- 建议：`QMenu(self)` 或存为 `self._tray_menu` 成员引用。

### [high] main.py:885-887 - 首页快捷入口导航到错误页面
- 现状：nav 行序：0 首页 / 1 配置向导 / 2 PDF 工作台 / 3 YAML 编辑器 / 4 任务监控 / 5 结果与复核 / 6 证据查看器 / 7 变更监控。代码：
  - `open_wizard → setCurrentRow(1)` ✓（配置向导）
  - `open_recent → setCurrentRow(3)` ✗（落到 **YAML 编辑器**，应为任务监控 row 4）
  - `open_results → setCurrentRow(4)` ✗（落到 **任务监控**，应为结果与复核 row 5）
- 问题：两个首页按钮打开错误页面；simple 模式下 row 5 还被 `delegates/theme.py:37-39` 隐藏，用户点了「结果与复核」看到的是监控页。
- 建议：`open_recent → row 4`、`open_results → row 5`；并把 row→page 映射抽成命名常量避免魔法数字。

### [high] design_system.py:463-470, 603, 624-632 - 界面缩放（interface_scale）被字体策略覆盖
- 现状：`apply_accessibility()`（accessibility.py:22-35）按 `scale` 设置了 app 字体；随后 `delegates/theme.py:98-111` 调 `apply_design_system()` → `ThemeManager.apply()` → `apply_font_strategy(app, scale=100)`（design_system.py:603），`apply_design_system` 兼容入口不转发 scale（624-632）。
- 问题：每次应用主题后 app 字体被无条件重置为 100% 比例 → 用户在设置里改的 80-160% 界面缩放对字体**无效**（成为"静默失效"设置项）。
- 建议：`ThemeManager.apply` 记录并复用最近一次 scale（或由 delegate 显式传入），`apply_design_system` 增加 `scale` 参数并透传。

### [high] home.py:352-354 - `_AIEnrichWorker` 线程无清理、不可取消
- 现状：`self._enrich_worker = _AIEnrichWorker(request, self, ...)` 以 HomePage 为 parent；`start()` 后无 `finished` 清理、无 `deleteLater`、无中断检查（run() 全程阻塞在网络调用 `compile_with_ai`）。再次触发会直接覆盖旧 worker 引用。
- 问题：AI 调用慢时关闭窗口 → HomePage 随主窗口销毁 → 仍在运行的 QThread 被销毁 → 崩溃；连点多次生成多个僵尸线程；旧 worker 结果可能在销毁后回传 `_on_ai_enriched` 触发 RuntimeError。
- 建议：启动前 `requestInterruption`/`wait` 旧 worker；连接 `finished` 到清理槽（`deleteLater`）；run() 内增加 `_thread_interrupted()` 检查点；AI 请求本身加超时。

### [high] help_center.py:98-101 - `_copy_example()` 对未知帮助 ID 崩溃
- 现状：`show_help()` 对未知 ID 用兜底 `HelpEntry` 展示（62-72），但**先**执行 `self._current_id = help_id`；`_copy_example()` 直接 `get_help(self._current_id).example`。
- 问题：当前显示兜底条目时点「复制示例」→ KeyError 在槽内未捕获 → PyQt6 经全局 excepthook 弹错误框。
- 建议：把兜底条目存为 `self._current_entry`，`_copy_example` 从成员取 `example`；或对 `get_help` 同样包 try/except KeyError。

### [high] async_workers.py:354 - `index_csv()` 传入不存在的 `max_rows` 参数
- 现状：`worker = CsvIndexWorker(path, max_rows=max_rows, parent=parent)`；而 `CsvIndexWorker.__init__(self, path, parent=None)`（202-204）无 `max_rows` 形参。
- 问题：`index_csv()` 一旦被调用即抛 TypeError（关键字参数不匹配）。当前仓库内无调用方，属潜在 API 崩溃点；且签名声明了 `max_rows` 语义与 B9 全量计数（216-221）不符。
- 建议：删除 `max_rows` 参数（或给 CsvIndexWorker 增加并真正用于截断），统一注释与实现。

### [high] main.py:1786-1809 - 无托盘图标时运行中任务被静默终止
- 现状：`closeEvent` 只在"有托盘图标 && 任务运行中"才弹"最小化到托盘/终止"确认框；无托盘（托盘不可用）时直接 `self._task_runner.stop()` 并关闭。
- 问题：无托盘环境下运行到一半的任务被无确认终止，用户丢失进度；对"独立本地 Worker 运行中"（worker_task_runner.py:104 明确声明关闭 GUI 不终止任务）的行为自相矛盾。
- 建议：无论是否有托盘，任务运行中都先弹确认；说明是否保留后台 worker 继续运行。

---

### [medium] main.py:1068-1073 - `_on_wizard_changed` 防重入标志形同虚设 + 定时器堆积
- 现状：`self._updating_editor = True` 后立即 `QTimer.singleShot(300, ...)` 再 `self._updating_editor = False`（同步复位）。
- 问题：标志在定时器触发前已复位，防重入完全失效；连续输入时每个变更都排一个 300ms 定时器且互不取消 → 累积多次全量 `update_from_config`，且可能引发"向导→编辑器→向导"的反馈循环（仅在编辑端有 `_updating_wizard` 保护，向导端没有）。
- 建议：保存定时器引用并先 `stop()` 再 `singleShot`（防抖）；把 `_updating_editor=True` 保持到 `_sync_wizard_to_editor` 执行完再复位。

### [medium] main.py:915（附）+2018-2024 - 全局异常钩子安装时机过晚
- 现状：`sys.excepthook = _global_exception_hook` 在 `MainWindow()` 与 `window.show()` 之后才安装；启动异常只落到外层 `except Exception` 打印 stderr 并 `return 1`。
- 问题：打包/冻结成 exe 后无控制台 → 启动闪退无任何提示；钩子内 `_show_error_dialog` 是模态对话框，在 Qt 槽展开异常期间弹出可能重入/卡死；定时器类槽反复异常会重复弹框。
- 建议：在创建 QApplication 前就安装钩子；钩子内限制弹框频率（去重）；启动段包一层弹窗报错。

### [medium] main.py:2001-2009 与 settings.py:22-27 - 冻结版存在两套设置存储路径
- 现状：`AppSettings` 冻结时写 `portable_data_root()/settings.ini`（settings.py:26）；main.py 冻结分支把 QSettings 默认路径设为 `portable_data_root()/.omnicrawler/settings`（IniFormat, UserScope），`TemplateLibraryDialog` 等用 `QSettings("OmniCrawler","GUIWorkbench")`（main.py:286）落到该目录。
- 问题：同一应用两套 INI/注册表，收藏模板、历史、主题等设置彼此割裂；开发版（注册表）与便携版（INI）行为也不一致。
- 建议：统一所有 QSettings 走 `AppSettings` 一个入口（如提供 `AppSettings.native(org, app)` 工厂）。

### [medium] main.py:1520 / 1567 - 同步耗时操作阻塞 UI 线程
- 现状：`_show_template_library()` 调 `discover_templates(force=True)`；`_show_preflight()` 同步跑 `run_preflight`；`_on_site_inspected` 内 `bundled_template_catalog(...)` 等均在主线程。
- 问题：模板量大或磁盘慢时 UI 冻结；`_inspect_site` 无并发保护，可多次点击叠加多个探测线程，`set_inspecting(False)` 会互相覆盖。
- 建议：模板发现走后台线程（复用 AsyncWorkerManager 模式）；探测期间禁用触发按钮/忽略重复请求。

### [medium] main.py:1306 - 插件路径解析越界未捕获
- 现状：`str(Path(inspection.path).resolve().relative_to(self._project_root.resolve()))`。
- 问题：插件路径在项目根之外（或解析为不同盘符）时 `relative_to` 抛 ValueError，槽内未捕获 → 全局钩子弹框；且 `resolve()` 对相对路径按 CWD 解析而非项目根。
- 建议：先 `resolve` 相对项目根，用 `os.path.commonpath` 判断并回退为绝对路径；包 try/except。

### [medium] main.py:1843-1875 - 拖放功能整体失效
- 现状：实现了 `dragEnterEvent`/`dropEvent`，但全工程 grep 无 `setAcceptDrops(True)`（QMainWindow 默认不接受拖放）。
- 问题：拖放 yaml/csv/zip 完全不会触发，功能是死代码；且 `dropEvent` 的 `.zip` 分支 `_import_config_package_from_path` 无异常保护。
- 建议：`__init__` 中 `self.setAcceptDrops(True)`；zip 导入包 try/except。

### [medium] main.py:1417-1435,1474-1495 - 定时任务「首次运行日期」选择后从未使用
- 现状：`_pick_date()` 把所选日期写入 `start_date_label.setText`；`add_current()` 组装 conditions 时只含 `require_ac/require_network/minimum_battery`，完全未读取日期。
- 问题：用户精心选择的日期被静默丢弃，功能名存实亡，属误导性 UX。
- 建议：把日期随 `store.add` 传入（如 `start_date` 条件）或移除该控件。

### [medium] design_system.py:202-210,337 - QSS 使用不支持的伪类/属性，焦点样式是死规则
- 现状：`QSS` 大量使用 `:focus-visible`（202-210）、`:!selected`（337）以及 `outline`/`outline-offset`（205-207）；Qt Style Sheets **不支持**这些伪状态与属性（仅支持 :focus、:hover、:selected 等；无 outline 属性）。
- 问题：键盘焦点可视化声明全部静默无效，无障碍焦点样式实际缺失。
- 建议：改用 `:focus` + `border`/`background` 实现焦点环；删除无效选择器。

### [medium] design_system.py:497-533 - `_SignalProxy.emit` 吞异常 + TypeError 静默降级
- 现状：回调抛 `TypeError` 时尝试无参重放；任意 `Exception` 仅 `logger.debug`；重入期间 emit 直接丢弃。
- 问题：业务回调（主题监听器）的错误被静默吞掉，无法到达全局 excepthook/用户；TypeError 回退会掩盖"槽签名写错"这类真 bug；重入丢信号可能在主题联动时漏通知。
- 建议：仅对可预期的回退做降级，其余异常至少 `logger.exception` 并可选上报；重入改为队列延迟处理而非丢弃。

### [medium] design_system.py:426-434（配合 145-166）- 色盲友好主题色值不在白名单
- 现状：`_TOKEN_HEX_WHITELIST` 仅收集 LIGHT/DARK/HIGH_CONTRAST 的色值；`theme_tokens(color_blind_friendly=True)` 生成的 `#0072B2/#56B4E9/#009E73/#E69F00/#D55E00/...` 不在白名单。
- 问题：设置 `OMNICRAWL_GUI_STRICT_HEX=1` 时，开启色盲模式 → `ThemeManager.apply` 内 `assert_no_raw_hex` 抛 ValueError，主题切换即崩溃（环境相关的潜伏缺陷）。
- 建议：把色盲变体色值加入白名单，或白名单改为由 `theme_tokens` 动态汇总。

### [medium] async_workers.py:41-64 等 - 中断后不产生任何结果信号，UI 可能永久停留在"加载中"
- 现状：各 worker 在 `isInterruptionRequested()` 时直接 `return`，既不 emit 业务信号也不 emit `failed`（仅内置 `finished` 触发清理）。
- 问题：调用方若只在 `finished_loading/failed` 里退出加载态，取消后状态永远卡住；取消时用户得不到任何反馈。
- 建议：取消分支统一 emit 一个 `cancelled`/`failed("已取消")` 信号，让 UI 复位。

### [medium] settings.py:329-330 - `sync()` 未防护 QSettings 已销毁
- 现状：其余方法均 `try/except RuntimeError`，唯 `sync()` 直接 `self._settings.sync()`。
- 问题：Qt 清理 QSettings（退出到启动器/测试重载场景，见 settings.py:58-64）后调用 sync 会抛 RuntimeError。
- 建议：`sync()` 同样包 try/except，并在失效时仅做 `_session_values` 落地。

### [medium] accessibility.py:22-35（配合 design_system） - 基准字体与点/像素混用
- 现状：`apply_accessibility` 以 `app.font().pointSizeF()` 为基准（首次调用时），后续乘 scale；而 `apply_font_strategy` 用 `FONT_SIZE["body"]*factor` 换算 `setPointSize`。
- 问题：两套缩放逻辑共享同一个 app 字体却互不感知，叠加主问题（interface_scale 被覆盖）后，字体基准/缩放在不同 DPI 与模式切换下会漂移；且 QSS 内 `font-size:14px` 与 QFont 的 `pointSize` 在 HiDPI 缩放下不一致，文本大小混乱。
- 建议：统一改为单一"基准像素 + 比例"源（建议 QSS 与 QFont 都用 device-independent pixel），DPI 变化时重算一次。

---

### [ux] main.py:1697,1871 - 直接 setCurrentIndex 绕过导航同步
- 现状：`_load_history_results` 与 CSV 拖放用 `self._stack.setCurrentIndex(3)` 直接切页，导航 `QListWidget` 高亮不随之更新。
- 问题：侧栏高亮与当前页不一致，误导用户当前位置；页面切换动画（PageTransitionController）也被绕过。
- 建议：统一走 `self._nav.setCurrentRow(...)` 触发 `_on_nav_changed`。

### [ux] main.py:1680-1697 - 历史结果无 CSV 时静默无响应
- 现状：`_load_history_results` 找不到 records.csv 时直接返回，无任何提示。
- 问题：用户点历史运行但无结果文件时界面毫无反应。
- 建议：Toast/状态栏提示"该运行无结果文件"。

### [ux] home.py:396-407 - 最近 URL 缓存写入无序集合
- 现状：`_save_recent_url` 用 `set` 去重后整体写回文件；`_load_recent_urls` 取"最后 10 条"。
- 问题：集合迭代顺序不保证 → 下次启动最近 URL 顺序随机，"最近"语义失效；并发多实例写同一文件可能丢数据。
- 建议：改为有序列表（去重置顶），限定长度，单文件写入加锁或原子替换。

### [ux] home.py:327,338 - `last_nl_request` 属性无人消费，PDF 模式指引是死胡同
- 现状：`_show_mode_dialog`/`_handle_pdf_mode` 设置 `self.setProperty("last_nl_request", ...)`，提示用户"请前往 PDF 工作台"；全工程 grep 无任何读取方。
- 问题：承诺的跨页面数据交接从未实现，用户跳转后信息丢失。
- 建议：在 PDF 工作台初始化时读取该属性填充，或移除提示文案。

### [ux] i18n 缺失（main.py / home.py / help_center.py）
- 现状：`home.py` 全文未引入 `_()`（所有文案硬编码中文）；`main.py` 大量控件文案裸写：L860 "Ⅱ 暂停/继续"、L300 "只看收藏"、L313 "☆ 收藏/取消收藏"、L1159-1166 preflight 对话框、L1010 cadence 映射（每周/每天…）、L1436-1446 电源/网络/电量行、L1239-1257 运行对比对话框等；`help_center.py` L25/L34/L48 等。
- 问题：项目声称 i18n（i18n.py + `_()`），但主界面大比例文案不参与翻译，切语言后大量中文残留，与设计文档不符。
- 建议：未走 `_()` 的文案统一接入 i18n 管线，或明确"仅中文"并移除 i18n 声明。

### [ux] help_center.py:87-96 - 搜索框每次输入都会 raise_() 抢焦点
- 现状：`textChanged → _refresh_results → setCurrentRow(0) → _select_result → show_help(..., reveal=self.isVisible())`；dock 可见时每次键入都执行 `self.show(); self.raise_()`。
- 问题：dock 若在浮动窗口/被其它面板遮挡，输入时反复置顶，干扰多窗口布局；每次搜索全量重建列表无防抖。
- 建议：`reveal` 仅首次执行；搜索结果用 QTimer 防抖。

### [ux] async_workers.py:229-267 - JsonlSearchWorker 中断后既不 found 也不 not_found
- 现状：中断时直接 `return`。
- 问题：查找证据时取消/关闭，调用方收不到任何结束信号，可能停留在"查找中"。
- 建议：中断时 emit `not_found` 或新增 `cancelled` 信号。

### [ux] design_system.py:271,312 - `:focus` 切换 1px 边框导致布局抖动
- 现状：`QPushButton:focus`/`QLineEdit:focus` 从 1px 变 2px 边框（不含 padding 补偿）。
- 问题：键盘 Tab 导航时控件尺寸变化引发整体布局跳动，键盘用户观感差。
- 建议：用 `border: 1px` + `outline`（若支持）或恒定 2px + 1px padding 补偿。

---

### [low] main.py:248-249,226 - `ConfigWizard._on_config_changed` 是空实现
- 现状：每个页面 config_changed 都被连到该 no-op（226 行），与 main 里 `_on_wizard_changed` 并存。
- 问题：死代码 + 每页多余信号连接；误导维护者以为此处有业务逻辑。
- 建议：删除该连接与空方法。

### [low] main.py:694 - 主窗口调用 delegate 私有方法 `_open_recent`
- 现状：`self._config_delegate._open_recent(filepath)` 直接调私有方法。
- 问题：破坏封装，delegate 重构易断。
- 建议：暴露公共方法（delegates/config_manager.py 已有 `_open_recent`，加个公开包装）。

### [low] main.py:970-973 - 访问 `_result_table._filepath`/`_chart_view._filepath` 私有属性
- 现状：F5 刷新直接读私有成员判空。
- 问题：与视图内部耦合，字段改名即坏。
- 建议：视图提供 `has_data()/refresh()` 公共接口。

### [low] main.py:1203-1206,1582-1585,1391-1394 - finished 线程上的 `deleteLater()` 可能不执行
- 现状：`thread.finished → 槽内 worker.deleteLater()`；worker 已 `moveToThread(thread)`，其事件循环已停。
- 问题：`deleteLater` 投递到已停止的线程事件循环，对象不会真正释放（轻微泄漏，进程退出时回收）。
- 建议：改用 `thread.finished.connect(worker.deleteLater)` 标准模式，或 `worker.setParent(None)` 后主线程删除。

### [low] main.py:286 - 模板收藏用独立 QSettings，与应用设置割裂（同上面 settings 双存储）
- 现状：`QSettings("OmniCrawler", "GUIWorkbench")` 直接裸用。
- 建议：经 AppSettings 统一管理。

### [low] main.py:915,1747 - 托盘单击无响应、菜单无父对象（见 high 条目）
- 现状：`_on_tray_activated` 只处理 DoubleClick。
- 建议：单击也恢复窗口；菜单问题见 high 项。

### [low] design_system.py:306 - QSS 选择器 `QTableWidget` 重复两次
- 现状：`... QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidget {{`。
- 问题：冗余，无功能影响。
- 建议：删除一个。

### [low] design_system.py:169-182 - `rgba_token_to_qcolor` 解析失败静默回退
- 现状：非 rgba 字符串回退 `QColor(0,0,0,40)`。
- 问题：令牌写错时阴影变半透明黑，无告警。
- 建议：解析失败打 warning 日志。

### [low] icon_registry.py:48 - `monitor_active` SVG 硬编码 `#d83b01`
- 现状：SVG 内 `fill="#d83b01" stroke="none"`；禁裸色守卫只扫描 QSS（design_system.py:444-446），SVG 不检。
- 问题：违反"所有颜色走令牌"的项目策略，深色主题下红点可能不协调。
- 建议：把该色并入 VisualTokens 或按主题计算。

### [low] icon_registry.py:58-64,86,117 - 死代码/无效参数
- 现状：`_color_map` 类属性从未使用；`cache_key` 传入 `_build_icon_cached` 但形参 `_cache_key` 未使用（lru_cache 实际按 (svg,size,fill) 缓存）；`colored.replace('fill="none"', 'fill="none"')` 是恒等替换。
- 建议：删除无意义代码，明确缓存键含义。

### [low] async_workers.py:196 - CsvIndexWorker docstring 与实现矛盾
- 现状：docstring 写"大文件仅统计前 100000 行"，实现（216-221）实际全量计数（B9 注释已说明）。
- 建议：更新 docstring。

### [low] shortcuts.py:79-93 - `rebind()` 不持久化
- 现状：仅 `setShortcut`，未写回 `AppSettings`，重启后还原。
- 问题：运行时改键"看起来生效"实则不保存。
- 建议：`rebind` 内同步 `settings.shortcuts` 并 sync。

### [low] motion_signal.py:44-47 / i18n.py:56-63 - 小瑕疵
- 现状：`_MotionSignal.is_reduced` 仅当 `notify` 被调用才更新（依赖 delegate 每次 refresh_accessibility 调用，目前成立）；i18n 的 `gettext.translation(fallback=True)` 使 try/except OSError 分支永不触发（不会抛）。
- 问题：均为健壮性注释性小问题。
- 建议：i18n 简化 try/except；motion_signal 初始值从 app property 读取兜底。

### [low] __init__.py / __main__.py - 无明显问题
- 现状：`__init__.py` 仅版本再导出；`__main__.py` 干净转发 `SystemExit(main())`。
- 建议：无需改动。

---

## 附：跨文件交叉验证结论（非问题）
- `MotionSignal.instance().notify(...)` 由 `delegates/theme.py:93` 在每次 refresh_accessibility 调用，home/status_indicator 的监听生效，无断链。
- `WorkerTaskRunner` 基于 subprocess backend + 750ms QTimer 轮询（worker_task_runner.py:54-56），`is_running`/`stop` 语义与 closeEvent 基本匹配；主要缺口是"无托盘时静默停止"（见 high 项）。
- `_format_yaml`（main.py:978）在 yaml_editor.py:528 存在，调用有效。
- home 页 AmbientHero 在 MainWindow 构建 `_refresh_accessibility`（main.py:545）之后创建（L882），初始 `omnicrawlerReducedMotion` 属性可用，时序正确。
