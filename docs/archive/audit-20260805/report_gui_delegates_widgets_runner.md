# 审查报告: gui delegates/widgets/runner

> 审查范围：`src/omnicrawl/gui/delegates/`（10 文件）、`src/omnicrawl/gui/widgets/`（9 文件）、`src/omnicrawl/gui/runner/`（6 文件）。
> 方式：逐行人工审查 + 交叉核对 `main.py` / `settings.py` / `config_model.py` / `runtime_paths.py` / `core/utils.py`。
> 环境：win32 / PyQt6。语法检查：`python -m py_compile` 全部 25 个文件 **通过（PY_COMPILE_OK）**。
> 说明：本报告只审阅，未修改任何文件。

---

## 汇总

| 严重级别 | 数量 | 说明 |
|--------|------|------|
| critical | 0 | 无导致崩溃/数据丢失的确定性缺陷 |
| high | 7 | GUI 线程阻塞、文本被污染、临时文件泄漏、日志内存无界、运行历史更新错误 |
| medium | 24 | 资源泄漏风险、状态卡死、主题/缩放不一致、异常逃逸 |
| low | 34 | 一致性问题、防御性编码、跨平台细节 |
| ux | 12 | 交互死区、无效控件、状态表达、启动副作用、i18n 不一致 |

**要点：**
- 7 个 high 中 3 个与"GUI 主线程同步执行子进程"相关（`check_omnicrawl` 最长 60s、`pip install` 最长 120s）。
- `ToastOverlay` 造成主窗口右侧约 360px 宽、近全高的**鼠标事件死区**（最严重的交互缺陷）。
- `log_console` 的 `_all_logs` 无上限增长，配合"切过滤全量重渲染"，长任务下会内存膨胀 + 界面冻结。
- `error_dialog.py:46` 与 `log_console.py:324` 的 `?` 正则会把普通问句文本错误改写，用户可见错误信息被污染。
- 运行历史（`run_controller.py:121`）用"当前配置的 task_id"更新，运行中切换配置会更新到错误记录甚至抛异常。
- `help_dialog.py` 每次打开选择器帮助都泄漏一个临时文件。

---

## 问题清单

## 1) delegates/_base.py

### [low] delegates/_base.py:26-29 - `__getattr__` 在 `_mw` 未初始化时可能无限递归
- **现状**：`__getattr__` 无条件 `return getattr(self._mw, name)`。正常属性查找失败时才会调用；而 `_mw` 本身也是通过 `__dict__` 存储的 property，因此常规路径不会递归。
- **问题**：若委托对象在 `_mw` 尚未赋值时被访问属性（如 `deepcopy`、`copy.copy`、pickle、或异常路径中构造了 delegate 但 `__init__` 未走完），`getattr(self._mw, ...)` 会再次触发 `__getattr__` → `RecursionError` 而非清晰的 `AttributeError`。同时该转发机制会**静默吞掉拼写错误**：delegate 上写错的 `self._xxx` 会透明转发到主窗口的同名属性，把 bug 推迟到深层调用点。
- **建议**：`__getattr__` 开头加 `if "_mw" not in self.__dict__: raise AttributeError(name)`；对转发结果不做类型校验，但可考虑在 delegate 类上加 `__slots__`/属性白名单降低误写风险。

---

## 2) delegates/config_manager.py

### [ux] config_manager.py:46-53 - `open_config` 不提示未保存更改，与 `new_config` 不一致
- **现状**：`new_config` 在存在字段/种子 URL 时会弹"未保存更改"确认；`open_config` 则直接弹文件选择框并加载。
- **问题**：用户辛辛苦苦改了配置后点"打开配置"，没有任何提示直接覆盖，属数据丢失隐患。`_open_recent`（最近文件菜单）同样无确认。
- **建议**：`open_config`/`_open_recent` 加载前复用与 `new_config` 相同的未保存检查。

### [ux] config_manager.py:30-32 - "dirty" 检测并非真实修改跟踪
- **现状**：以 `mw._config.fields or mw._config.seed_urls` 判断是否有更改。
- **问题**：只要配置非空（哪怕刚打开、未改一个字）就弹"未保存"提示；反之，若在默认空配置上修改过但没有字段/种子（例如只改了 `project_name`），则不提示直接丢失。
- **建议**：基于 `_config_path` + 内容哈希/序列化比较做真实 dirty 跟踪（例如保存时记录 `to_yaml(config)` 快照，操作前对比）。

### [medium] config_manager.py:89,132 - 用 `project_name` 拼默认文件名，Windows 非法字符
- **现状**：`save_config_as` 用 `f"{project_name}_{timestamp}.yaml"`、`export_config_package` 用 `f"{project_name}.zip"`。
- **问题**：`project_name` 可含 Windows 非法文件名字符（`\ / : * ? " < > |`），会导致另存对话框初始名无效或写出错。
- **建议**：生成默认名时对 `project_name` 做非法字符清洗（正则替换）。

### [low] config_manager.py:80 - `save_config` 后不刷新"最近文件"菜单
- **现状**：`save_config` 调用了 `add_recent_file`，却未像 `_open_recent`/`save_config_as` 那样调用 `mw._refresh_recent_menu()`。
- **问题**：行为不一致；保存新建文件后最近文件菜单不更新。
- **建议**：补齐 `_refresh_recent_menu()`。

### [medium] config_manager.py:175-178 - 导入配置包后未重绑控制器/未加入最近文件
- **现状**：`_import_from_path` 成功后将 `_config_path` 置 `None`、重建向导，但**不调用** `_bind_application_controllers()`，也不 `add_recent_file`。
- **问题**：与 `open/save/save_as` 不一致；导入的配置无法挂上任务控制器，可能导致后续"运行"在 `_config_path` 为空时异常路径行为。
- **建议**：导入成功后与 `_open_recent` 对齐，重绑控制器并加入最近文件。

### [medium] config_manager.py:216-218 - 配置历史恢复无异常保护
- **现状**：`snapshot(...)` → `restore(...)` → `load_yaml(...)` 三个调用都在 try 之外。
- **问题**：任一失败（损坏快照、IO 错误）异常直接逃逸出对话框槽函数，用户只看到 Qt 打印的 traceback，无任何界面反馈。
- **建议**：包一层 try/except，失败时 `QMessageBox.critical` + 回滚信息。

### [ux] config_manager.py:194,197 - 硬编码中文绕过 i18n
- **现状**：`dialog.setWindowTitle("配置历史与恢复")`、`QLabel("恢复前会自动备份…")` 未走 `_()`。
- **问题**：与本文件其它字符串风格不一致，无法翻译。
- **建议**：统一用 `_()`。

### [low] config_manager.py:200 - 历史版本时间显示依赖字段类型
- **现状**：`f"{version.get('created_at', '')}"`。
- **问题**：若 `created_at` 是 `datetime`/`None` 则显示对象 repr 或空串，列表项可读性差。
- **建议**：统一格式化（如 `.strftime` 或 `str(...)[:19]`）。

---

## 3) delegates/env_checker.py

### [high] delegates/env_checker.py:52 - 环境检测同步阻塞 GUI 线程（最长 60s）
- **现状**：`check_environment` 直接调用 `runner/env_checker.check_omnicrawl`，内部是 `subprocess.run(..., timeout=...)`，冻结模式下 timeout 可达 **60s**。被 `run_task`（run_controller.py:29）、`recheck_env`、`on_first_launch`、重试按钮等多处 GUI 路径直接调用。
- **问题**：环境检测期间主界面完全冻结（无响应/无法取消）。杀软慢扫或冷启动时体验极差。
- **建议**：改用 `QThread`/`QFuture` + 信号回传结果，UI 上显示"检测中…"并允许取消；或至少将首次启动检测放到后台并限时。

### [high] delegates/env_checker.py:166 - `try_auto_install` 在 GUI 线程跑 pip（最长 120s）
- **现状**：`show_env_setup_dialog` 的"自动安装"分支同步调用 `try_auto_install`，其内部 `subprocess.run(..., timeout=120)`。
- **问题**：安装期间界面冻结最长 2 分钟，用户无法取消，且失败信息极迟才显示。
- **建议**：移入后台线程/worker，进度用 toast/进度条回传。

### [ux] delegates/env_checker.py:113-116 - 欢迎对话框"不再显示"复选框无效
- **现状**：`cb = QCheckBox("不再显示")`，`msg.setCheckBox(cb)`，`msg.exec()` 后**从未读取 `cb.isChecked()`**。
- **问题**：复选框是死 UI——勾不勾没有任何区别（首次启动与否只由 `on_first_launch` 里 `is_first_launch` 决定）。
- **建议**：读取勾选状态写入设置（例如 `settings` 增加 `welcome_shown`），或移除复选框避免误导。

### [medium] delegates/env_checker.py:36 - 取消"自选数据目录"静默回退便携模式
- **现状**：`configure_data_mode("custom", directory) if directory else configure_data_mode("portable")`。
- **问题**：用户点了"自选数据目录"又取消对话框后，静默变成便携模式，无任何说明。
- **建议**：取消时回退到"完全便携"并提示，或保持上一次选择。

### [medium] delegates/env_checker.py:69-76 - `switch_project` 后多处状态过期
- **现状**：只更新 `_project_root`、`_settings.project_root` 和项目标签。
- **问题**：`_task_runner._project_root`、`ResourceMonitor._project_root`（磁盘监控）、`AutosaveManager` 根目录等仍指向旧目录——切目录后磁盘/自动保存/worker 落盘位置全部过期。
- **建议**：切换时统一更新 task_runner / resource_monitor / autosave 的根目录，必要时重建。

### [low] delegates/env_checker.py:202-208 - 占位符选择器替换用 `if/elif` 链
- **现状**：对含 `{{title_selector}}`、`{{link_selector}}`、`{{date_selector}}` 的字段逐条 `elif` 替换。
- **问题**：一个字段若同时含多个占位符，只替换命中第一条的分支，其余保留。
- **建议**：改为对每个占位符独立 `if`，或统一正则替换。

---

## 4) delegates/error_dialog.py

### [high] delegates/error_dialog.py:46 - `?` 正则误伤普通文本
- **现状**：`re.sub(r'(\?[^\s\'"<>]*)', '?[REDACTED]', text)` 会把**任意**位置出现的问号（包括普通句子的"是否继续？"、数学式、注释）替换成 `?[REDACTED]`。
- **问题**：错误详情/复制内容中的正常问句被破坏，用户看到莫名 `?[REDACTED]`，隐私脱敏反而制造噪音；且 URL 已在上一行脱敏，此行主要命中非 URL 文本。
- **建议**：只对紧跟在 URL/查询串之后的问号脱敏（与 URL 规则合并），或用更窄上下文（如 `https?://\S*\?[^\s]*`）。

### [low] delegates/error_dialog.py:20 - 非 except 上下文 `format_exc()` 显示 "NoneType: None"
- **现状**：`tb = traceback.format_exc()`。
- **问题**：若 `show_error_dialog` 在不处于 except 块时被调用（如异步回调、重试回调外），详细文本只有 "NoneType: None"，误导排障。
- **建议**：`exc` 存在时用 `"".join(traceback.format_exception(type(exc), exc, exc.__traceback__))`，否则提示"无异常上下文"。

### [low] delegates/error_dialog.py:29 - retry 回调异常无防护
- **现状**：`retry_btn.clicked.connect(lambda: (msg.close(), retry_callback()))`。
- **问题**：`retry_callback()` 抛异常会直接冒泡到 Qt 槽（PyQt 打印 traceback），且已 close 的对话框无法再给用户反馈。
- **建议**：在 lambda 内 try/except 并 fallback 到日志或新错误弹窗。

### [low] delegates/error_dialog.py:28-36 - 生产代码使用 assert
- **现状**：`assert retry_btn is not None` 等 4 处。
- **问题**：`python -O` 下 assert 被剥离，若按钮获取失败会静默拿到 None 继续崩在后续调用。
- **建议**：改用显式 `if x is None: raise` 或忽略（影响极低）。

---

## 5) delegates/help_dialog.py

### [high] delegates/help_dialog.py:32-35 - 每次打开选择器帮助泄漏一个临时文件
- **现状**：`tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False)`，写出后仅 `close()`，从不删除。
- **问题**：每次点击"选择器语法帮助"都在 `%TEMP%` 新增一个 `.html` 文件，长期使用积累垃圾文件。
- **建议**：改用 `TemporaryDirectory` 统一清理；或浏览器打开后按需延迟删除（`QTimer.singleShot`）；或直接读取 `html` 到 `QTextBrowser` 内嵌展示。

### [medium] delegates/help_dialog.py:31 - `html.replace` 精确匹配主题注入
- **现状**：`html.replace("<html lang=\"zh-CN\">", f'<html lang="zh-CN" data-theme="{theme}">')`。
- **问题**：若帮助文件首标签带属性或换行变体，替换静默失效，`data-theme` 不注入。
- **建议**：用正则 `re.sub(r'<html[^>]*>', ...)` 兜底。

### [low] delegates/help_dialog.py:91-92 - 能力报告结构无防御
- **现状**：`item['installed']`、`report["modules"]`、`item.get('path')`。
- **问题**：`capability_report()` 若字段改名/缺失直接 `KeyError` 崩对话框。
- **建议**：用 `.get()` + 默认值。

---

## 6) delegates/menu.py

### [ux] delegates/menu.py:47,81,85,89,93,97,101,102 - 大量硬编码中文绕过 i18n
- **现状**：`"配置历史与恢复..."`、`"运行前检查与小样本试跑..."`、`"打开统一错误中心"`、`"对比两次运行..."`、`"插件管理与权限..."`、`"PDF 页面框选字段..."`、`"录制网页操作..."` 及 tooltip 均未包 `_()`。
- **问题**：与同文件其它条目（`_("打开配置(&O)...")` 等）风格不一致，无法本地化。
- **建议**：统一包 `_()`。

### [low] delegates/menu.py:159-161 - DND 动作勾选状态与 `_dnd_mode` 可能失步
- **现状**：`dnd_action.setChecked(mw._dnd_mode)`，`toggled.connect(mw._toggle_dnd)`。
- **问题**：若其它路径（托盘、自动 DND）修改 `_dnd_mode`，菜单勾选不会同步；反向同理。
- **建议**：DND 状态收敛到单一设置源，并在设置变更时刷新 `dnd_action.setChecked`（注意防信号回环）。

### [low] delegates/menu.py:148,154,168 - 直接 `setattr` 设置项，与 delegate 封装不一致
- **现状**：`toggled.connect(lambda v: setattr(mw._settings, 'auto_open_result', v))`。
- **问题**：绕过了 ThemeManager/设置校验层；若未来设置需要副作用（如刷新状态栏、重置 autosave）将漏掉。
- **建议**：走统一 `_set_accessibility_option` 风格的委托方法。

### [low] delegates/menu.py:98,116,124,203 - lambda 形参风格不一致（PySide 移植风险）
- **现状**：`triggered` 信号发射 `bool`，部分 lambda 显式接 `checked`/`_checked`，部分为 0 参 `lambda:`。
- **问题**：PyQt6 会自动丢弃多余参数故均正常；但迁移 PySide6 时 0 参 lambda 会收到 bool 抛 `TypeError`。当前不是 bug，仅一致性/可移植性隐患。
- **建议**：统一为 `lambda _=False: ...` 或在文档注明依赖 PyQt 的参数裁剪行为。

---

## 7) delegates/run_controller.py

### [high] delegates/run_controller.py:121 - 用"当前配置 task_id"更新历史，运行中切换配置会错记录/抛异常
- **现状**：`run_task` 记录时用 `mw._config.task_id`；`on_task_state_changed` 更新历史也读 `mw._config.task_id`。而 worker 侧 `task_finished` 发射的是启动时捕获的 `_current_task_id`。
- **问题**：任务运行中用户可点"新建/打开配置"（运行按钮禁用但新建/打开不禁用），`_config` 被替换，`task_id` 变成新配置的——结束时 `update_record(new_task_id, ...)` 更新到**错误记录**，或该 task_id 不在历史中直接抛异常，任务结果丢失。
- **建议**：启动时把 `task_id` 存入 `mw._running_task_id`，`on_task_state_changed`/`update_record` 使用它；或在运行期间禁用新建/打开/导入配置。

### [ux] delegates/run_controller.py:71 - 启动失败仍切换到结果页
- **现状**：`ok` 为 False 时已回置按钮并提示"启动失败"，但随后无条件 `mw._stack.setCurrentIndex(2)`。
- **问题**：启动失败却跳到结果/运行页，用户看到空结果或上一轮残留，误导。
- **建议**：`ok` 为 False 时保持当前页，仅 toast 提示失败。

### [medium] delegates/run_controller.py:53-55,118-120 - 每次运行新建 QTimer，旧实例可能泄漏/残留
- **现状**：`run_task` 无条件 `mw._task_elapsed_timer = QTimer(mw)` 并 start；仅在 `state in ("finished","error")` 时 stop 并置 None。`_task_start_time` 完成后也不复位。
- **问题**：若 stop 流程没走到 finished/error（如 `stop()` 异常），旧 timer 继续每秒触发 `_update_elapsed`；重复运行会叠加多个 timer。
- **建议**：run_task 开头先停旧 timer；finished 分支把 `_task_start_time` 一并置 None。

### [medium] delegates/run_controller.py:73-78 - `stop_task` 失败时状态永久卡在"正在停止..."
- **现状**：`stop_task` 调 `_task_runner.stop()`（内部异常仅 `log_line`），随后禁用按钮、状态置"正在停止..."。
- **问题**：若 backend 已死/网络失败，`state_changed` 永不再发，按钮禁用、状态卡死。
- **建议**：`stop()` 返回是否成功/抛出时，失败回退状态并重新启用 run 按钮。

### [low] delegates/run_controller.py:137-139 - `on_task_finished` 空槽
- **现状**：`@pyqtSlot(str, int)`，函数体 `pass`，注释说由 state_changed 处理。
- **问题**：`task_finished` 信号的 `exit_code`（含 cancelled→1）被完全忽略；若某状态变更路径漏发 `state_changed`（如 worker 在 attach 前已结束），完成事件会静默丢失。
- **建议**：至少把 exit_code 写入日志/历史，或在槽内兜底触发一次状态刷新。

### [medium] delegates/run_controller.py:28-31 - 运行前再次阻塞式环境检测
- **现状**：`if not mw._omnicrawl_available: mw._env_checker.check_environment(silent=False)`（见 env_checker 问题）。
- **问题**：与 high 级 env_checker.py:52 同源——运行前最长可冻结 60s。
- **建议**：环境状态在启动时已后台检测，运行前仅读缓存标志，或把复检放到后台。

---

## 8) delegates/theme.py

### [medium] delegates/theme.py:31-39 - 硬编码导航索引
- **现状**：`mw._nav.item(2/5/6)` + `assert` 非 None。
- **问题**：`QListWidget` 行序一旦调整（增删页面），索引错位或 assert 崩溃；简单/开发者模式的显隐逻辑全依赖魔数。
- **建议**：给导航项命名（objectName/setData）后按名查找。

### [medium] delegates/theme.py:57 - 假设"stack 页 1 == 导航行 1 == 向导"
- **现状**：`if mode == "simple" and mw._stack.currentIndex() == 1: mw._nav.setCurrentRow(1)`。
- **问题**：硬编码行号与页面含义耦合，重构易错。
- **建议**：用命名常量/枚举映射。

### [low] delegates/theme.py:105-106 - "跟随系统"主题检测依赖已应用过的 palette
- **现状**：`theme = "dark" if app.palette().color(...Window).lightness() < 128 else "light"`。
- **问题**：若之前应用过深色主题，palette 已是深色，切到"system"时会检测回深色而不是操作系统主题；且不会响应系统实时切换。
- **建议**：读注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize` 的 `AppsUseLightTheme`（win32）作为权威来源。

### [medium] delegates/theme.py:113-117 - 界面缩放对固定像素控件不生效
- **现状**：`set_interface_scale` 依赖 `apply_accessibility` + 令牌 stylesheet。
- **问题**：日志控制台 `QFont(...,10)`、过滤器按钮 `setFixedSize(56,24)`、状态灯 `setFixedSize`、HelpTooltip `setFixedSize(32,32)`、toast 图标等使用硬编码像素，125%/150% 缩放后这些控件尺寸不变，界面比例失调。
- **建议**：把像素尺寸也改为令牌/相对单位，或在缩放变化后统一 repolish。

---

## 9) delegates/toolbar.py

### [ux] delegates/toolbar.py:75-79 - 启动时设置 combo 触发 `currentIndexChanged` → 启动即弹 toast/改配置
- **现状**：`for ... setCurrentIndex(...)`；当 `config.resource_profile`（默认 "balanced"）与 combo 默认项不同时会触发 `_change_resource_profile` → `ToastManager.info` + 写 `passthrough["resources"]`。
- **问题**：程序启动瞬间弹出 toast，且可能在 UI 尚未完备时改配置。默认组合框第一项是"省电(economy)"，而配置默认 "balanced"，**每次启动几乎必触发**。
- **建议**：初始化 combo 时先 `blockSignals(True)`，设置完后再恢复。

### [ux] delegates/toolbar.py:40,63 - 硬编码按钮文本绕过 i18n
- **现状**：`"Ⅱ 暂停"`、`"✓ 试跑检查"`、tooltip `"检查依赖、磁盘与配置…"` 未走 `_()`。
- **建议**：统一 `_()`。

---

## 10) widgets/calendar_popup.py

### [ux] widgets/calendar_popup.py:30-57 - 日历组件无主题令牌，暗色主题下发白
- **现状**：使用原生 `QCalendarWidget`，未接入 `ThemeManager` 令牌，样式跟随系统 palette。
- **问题**：深色主题下弹出一块亮色日历，视觉割裂。
- **建议**：通过 QSS 覆盖日历控件配色（读取令牌），并随 `theme_changed` 刷新。

### [low] widgets/calendar_popup.py:70-71 - 宽泛 `except Exception` 静默
- **现状**：`set_date` 解析失败仅 `logger.debug`。
- **问题**：可接受但无任何用户提示；解析失败时日历停在今天，行为不明确。
- **建议**：保持 debug 日志即可，或在异常时清空选中。

---

## 11) widgets/empty_state.py

### [low] widgets/empty_state.py:74 - 只有 label 没有 callback 时按钮静默消失
- **现状**：`if action_label and action_callback:` 才建按钮。
- **问题**：调用方传了 label 忘传 callback 时，界面不显示任何按钮也无提示。
- **建议**：参数校验并在开发日志提示。

### [low] widgets/empty_state.py:119 - `set_message` 无法清空描述
- **现状**：`if description: self._desc_label.setText(description)`。
- **问题**：想恢复空描述无法通过传 `""` 实现。
- **建议**：无条件 setText。

---

## 12) widgets/form_feedback.py

### [medium] widgets/form_feedback.py:30-41 - `parent=None` 时动画组可能被 GC
- **现状**：`QSequentialAnimationGroup(parent)`，`parent` 默认 None；`group` 是局部变量，仅在 `finished` 里 `deleteLater`。
- **问题**：若无父对象且没有其它强引用，`shake_widget` 返回后 Python 可能回收 `group` → 动画中断/异常。当前调用方若传父控件则安全，但签名默认值掩盖了风险。
- **建议**：把动画组挂到 widget 自身（`QSequentialAnimationGroup(widget)`）或保存引用到 widget 属性，结束后清理。

### [medium] widgets/form_feedback.py:19-27 - 对布局内控件动画 `pos` 会与布局冲突
- **现状**：`base_pos = widget.pos()`，`QPropertyAnimation(widget, b"pos", ...)`。
- **问题**：由布局管理的控件位置由布局决定；动画期间若发生 relayout（尺寸变化/滚动）会被布局拉回，出现抖动或不抖。
- **建议**：改在容器/包裹层上动画，或使用 `geometry` 属性并临时加 `QGraphicsProxyWidget` 方案。

### [low] widgets/form_feedback.py:44-52 - `set_error_style` 无 message 时残留旧 tooltip
- **现状**：仅当 `if message:` 才 setToolTip。
- **问题**：先设过错误 tooltip，再以空 message 调用时旧提示残留。
- **建议**：空 message 时同时清 tooltip（或与 `clear_error_style` 合并）。

---

## 13) widgets/help_tooltip.py

### [medium] widgets/help_tooltip.py:43,53,104-107 - `get_help` 返回 None 未防御
- **现状**：`self._entry = get_help(help_id)`，随后直接 `self._entry.short(...)` / `self._entry.title`。
- **问题**：help_id 未注册时 `get_help` 若返回 None → `AttributeError` 崩在控件构造或点击处。
- **建议**：`if self._entry is None:` 降级为通用提示或日志 + 显示内置文本。

### [ux] widgets/help_tooltip.py:4,55-57 - 文档宣称 F1 支持但无实现
- **现状**：docstring/悬停提示写"按 F1 打开帮助中心"，但类内没有任何 `keyPressEvent`/短捷键处理。
- **问题**：文档与实现不符，用户按 F1 无反应。
- **建议**：实现 F1（Qt.Key_F1 → `_show_help`），或删除提示文案。

---

## 14) widgets/log_console.py

### [high] widgets/log_console.py:151,211-221 - `_all_logs` 无上限 + 切过滤全量重渲染
- **现状**：`append_log` 无条件 `self._all_logs.append(...)`；`_set_filter` 先 `clear()` 再对**全部**缓存逐条 `_append_to_editor`。
- **问题**：长任务（爬虫逐行日志）下 `_all_logs` 可膨胀到十万百万级（内存无界）；期间点过滤按钮会一次性重绘全部行 → 界面冻结数秒。
- **建议**：`_all_logs` 设上限（如保留最近 2 万条）或改用按需重放；过滤重渲染时 `setUpdatesEnabled(False)` + 批量插入文本，完后再启用。

### [medium] widgets/log_console.py:184-188 - 逐行 `append` + `strftime` 高频日志卡 GUI
- **现状**：每条日志 `datetime.now().strftime` + `QTextEdit.append`（内部 relayout/滚动更新）。
- **问题**：worker 输出密集时（每行 750ms 轮询已缓冲多行），主线程逐行刷新成为瓶颈。
- **建议**：用去抖定时器（如 100ms）批量追加，或直接操作 `QTextDocument` 并关闭自动滚动。

### [medium] widgets/log_console.py:196-209 - 裁剪不足 + 逐块选择 O(n)
- **现状**：单次裁剪固定删除 TRIM_HEAD=2000 行；`_trim_timer` 是 single-shot 100ms。
- **问题**：一次 burst 若超过 5000 行（100ms 内塞入 8000 行），裁剪 2000 后仍有 6000 行 > MAX_BLOCKS，而 timer 已触发过、下次追加才再裁 → 文档长期超标；且每次用 `KeepAnchor` 逐行移动 2000 次开销大。
- **建议**：用 `cursor.movePosition(StartOfBlock)` + `movePosition(Down, KeepAnchor, TRIM_HEAD)` 批量；循环裁到 `blockCount() <= MAX_BLOCKS`。

### [medium] widgets/log_console.py:256-275 - 搜索高亮不清理，重复搜索残留
- **现状**：`_search_highlight` 直接 `mergeCharFormat`，无先清旧高亮。
- **问题**：多次搜索后旧高亮叠加残留，视觉混乱；无"取消高亮"入口。
- **建议**：搜索前用 `QTextCursor` 恢复 `Qt.NoTextFormat` 或保留单独高亮层。

### [low] widgets/log_console.py:287-297 - 导出与显示格式不一致（无时间戳）
- **现状**：界面行含 `[HH:MM:SS]`，导出文件只有 `[LEVEL] 内容`。
- **问题**：排障时导出日志无法对应时间线。
- **建议**：导出时补时间戳（或统一格式）。

### [low] widgets/log_console.py:127,163 - 字体双来源
- **现状**：`QFont(FONT_FAMILY_MONO.split(", ")[0], 10)` 显式设置，随后 stylesheet 又 `font-family: {FONT_FAMILY_MONO}; font-size: ...`。
- **问题**：两处可能冲突（`split(", ")` 对带引号字体会切出 `"'Courier New'"` 这类无效首字体）。
- **建议**：只用 stylesheet 一处定义字体。

---

## 15) widgets/resource_monitor.py

### [medium] widgets/resource_monitor.py:44 - psutil 缺失时整个组件隐藏，磁盘监控一并丢失
- **现状**：`self.setVisible(_PSUTIL_AVAILABLE)`。
- **问题**：磁盘空间只用 `shutil`，本可独立工作；psutil 缺失却把磁盘信息也隐藏了。
- **建议**：拆成两个可独立显示的标签。

### [medium] widgets/resource_monitor.py:113 - 每 3 秒 `shutil.disk_usage` 在 GUI 线程；`_project_root` 固定过期
- **现状**：`refresh` 每 3s 在 GUI 线程执行 `shutil.disk_usage(self._project_root)`；`_project_root` 构造时固定。
- **问题**：项目根在慢盘/网络共享上会周期性卡顿；`switch_project` 后仍显示旧目录磁盘。
- **建议**：磁盘统计降频或移到后台；提供 `set_project_root` 并在切换项目时调用。

### [low] widgets/resource_monitor.py:121 - 裸 `except Exception` 静默
- **现状**：磁盘异常 → 显示 "--"，无日志。
- **建议**：加 `logger.debug(exc_info=True)`。

### [low] widgets/resource_monitor.py:92-108 - 只统计 worker 主进程 RSS
- **现状**：`psutil.Process(self._pid).memory_info().rss`。
- **问题**：爬虫/浏览器子进程内存不计入，"内存 > 2GB 警告"形同虚设。
- **建议**：用 `proc.children(recursive=True)` 累加，或改用进程树内存。

---

## 16) widgets/status_indicator.py

### [ux] widgets/status_indicator.py:66-84 - stopping/paused/retrying 无独立颜色
- **现状**：`_colors` 只有 idle/running/finished/error，`paintEvent` 对未知状态回落灰色（idle 色）。而 `RunController.on_task_state_changed` 会把 `stopping` 直接赋给 `state`。
- **问题**：文本说"正在安全停止"，指示灯却是灰色（空闲），状态误导。
- **建议**：补 stopping（黄/橙）、paused（黄）、retrying（橙）配色与 tooltip 文案。

### [low] widgets/status_indicator.py:37-39 - `setattr` lambda 无类型/防护
- **现状**：`MotionSignal.reduced_motion_changed.connect(lambda v: setattr(self, "_reduced_motion", v))`。
- **建议**：改为具名方法便于维护与类型标注。

---

## 17) widgets/toast.py

### [ux] widgets/toast.py:227,250-264 - ToastOverlay 造成主窗口右侧鼠标事件"死区"
- **现状**：overlay 是主窗口的无布局子 QWidget，`WA_TransparentForMouseEvents` 显式设 False（"接收鼠标事件"），几何覆盖 `parent.width()-w-8, 48, w, height-64`（w=min(360, 宽/2)），并 `raise_()` 置顶；`show()` 在有 toast 时一直显示。
- **问题**：overlay 大片透明区域没有 toast 子项，点击会落在 overlay 上被吞掉，**下方主界面（结果页、状态区、滚动条等）收不到点击**——形成常驻的右侧隐形死区（只要有一条 toast 存在就生效）。
- **建议**：overlay 自身设 `WA_TransparentForMouseEvents = True`，仅 toast 子项单独取消该属性；或把 overlay 尺寸收缩到只包住实际 toast，避免全高拦截。

### [low] widgets/toast.py:147,164 - 子控件 `windowOpacity` 淡入在多数平台不生效
- **现状**：`QPropertyAnimation(self, b"windowOpacity", ...)`，self 是 overlay 的子 QFrame。
- **问题**：`setWindowOpacity` 对非顶层窗口通常无效（代码注释已承认关闭动画靠 fallback timer 兜底），淡入纯属装饰、不产生淡出效果。
- **建议**：统一用 fallback 方式（立即显示/隐藏），或把 toast 提升为顶层窗口再动画。

### [low] widgets/toast.py:210-215 - action 回调异常未捕获
- **现状**：`finally: self._start_close()`，但异常会继续冒泡到 Qt 槽。
- **建议**：try/except + `logger.exception`，保证 toast 仍正常关闭。

### [low] widgets/toast.py:283-284 - 去重返回"最后一条"而非匹配项，类型注解可返回 None
- **现状**：`return self._toasts[-1] if self._toasts else None`；`show_toast` 注解 `-> Toast`。
- **问题**：500ms 内重复消息返回的是列表末尾（最新）toast，未必是同一条；且返回类型含 None 与注解不符。
- **建议**：记录匹配的 toast 引用；类型注解改为 `Toast | None`。

### [medium] widgets/toast.py:288-291 - 超上限直接丢弃最旧 toast，未发 closed、操作回调静默丢失
- **现状**：`len(self._toasts) >= self._max_toasts` 时 `pop(0)` + `deleteLater()`，不触发 `closed`，也不调用其 action 回调。
- **问题**：第 6 条起的 toast 如果带"操作"按钮（如错误重试），按钮和回调无提示被丢弃；也不通知外层清理。
- **建议**：丢弃前显式断开/触发回调的"取消"语义，或改为滚动移除并提示。

---

## 18) runner/env_checker.py

### [high] runner/env_checker.py:63-91 - `check_omnicrawl` 同步 `subprocess.run` 且超时最长 60s
- **现状**：`subprocess.run([...], timeout=timeout, capture_output=True)`；`timeout = 10 if bundled else (60 if is_frozen() else 10)`。
- **问题**：该函数被 GUI 主线程直接调用（见 delegates/env_checker 与 run_controller），冻结包未探测到 bundled 时最长卡 60s；每次"重试检测"都如此。
- **建议**：封装为异步任务（QThread/future），GUI 侧只接收信号；或把探测降级为"文件存在 + 短超时(3s)"的强信号策略（已用于 bundled 场景，可推广）。

### [low] runner/env_checker.py:70-79 - stderr 被当版本号
- **现状**：`version_output = result.stdout.strip() or result.stderr.strip()`；bundled 且 rc!=0 时仍返回 `True, stderr内容`。
- **问题**：stderr 若是一段报错文本会被当作"版本号"写入设置并在 About 显示。
- **建议**：rc!=0 时对版本串做格式校验（含版本号模式）再采用。

### [low] runner/env_checker.py:245-260 - `try_auto_install` 同步 pip（120s）
- **现状**：`subprocess.run([sys.executable, "-m", "pip", "install", "-e", ...], timeout=120)`。
- **问题**：见 delegates/env_checker.py:166——GUI 主线程直接调用会冻结 2 分钟。
- **建议**：改为异步并在 GUI 侧禁用重复点击/加进度提示。

---

## 19) runner/headless_runner.py

### [medium] runner/headless_runner.py:121-126 - 非 KeyboardInterrupt 异常时子进程未终止（孤儿进程）
- **现状**：只有 `KeyboardInterrupt` 分支 `terminate/wait/kill`；通用 `except Exception` 仅打印并 return 1；`finally` 只关 stdout。
- **问题**：流式读取期间若发生其它异常，worker 子进程继续在后台运行且没人管理。
- **建议**：`finally` 中若进程仍存活先 terminate（可带短 wait 再 kill）。

### [low] runner/headless_runner.py:92-103 - stdout 阻塞式读取无超时
- **现状**：`for line in process.stdout` 一直阻塞到进程退出。
- **问题**：子进程 hang 时 headless 调用无超时退出机制（仅 KeyboardInterrupt）。
- **建议**：按 CLI 定位可接受；如需健壮，改用带超时的 select/线程读取。

### [low] runner/headless_runner.py:43-55 - ANSI 颜色无条件输出
- **现状**：错误/警告着色硬编码 `\033[...m`，不检测是否 TTY。
- **问题**：CI 日志管道中会出现转义序列垃圾（部分环境）。
- **建议**：`sys.stderr.isatty()`/`NO_COLOR` 时禁用颜色。

---

## 20) runner/log_parser.py

### [medium] runner/log_parser.py:79-80 - `on_progress` 回调异常未捕获
- **现状**：`parse_line` 只在 `try` 里捕 `(ValueError, IndexError)`，而回调 `self._on_progress(...)` 也在其中。
- **问题**：回调抛 `KeyError`/`TypeError` 会直接冒出，影响调用方循环（甚至 worker 线程崩溃）。
- **建议**：回调单独 try/except 并日志，解析器不应因回调失败中断。

### [low] runner/log_parser.py:26-30 - 中文统计正则覆盖面窄
- **现状**：`records` 模式 `(?:提取|extracted?)` 匹配不到"提取了 5 条"。
- **问题**：常见中文变体漏统计（仅影响统计展示精度）。
- **建议**：补充 `提取了?`、`共提取` 等变体。

---

## 21) runner/remote_runner.py

### [low] runner/remote_runner.py:25-47 - 预留桩直接抛 NotImplementedError
- **现状**：三个方法均抛 `NotImplementedError`，但无调用方防护。
- **问题**：未来若有代码路径不经检查调用 `submit/status/cancel`（如菜单项绑定），会直接抛异常。
- **建议**：保持现状即可；若接入 GUI，加"该功能尚未发布"的提示层，避免裸异常。

---

## 22) runner/worker_task_runner.py

### [low] runner/worker_task_runner.py:15-30 - `_derive_worker_command` 只认 `.exe`
- **现状**：仅查找 `omnicrawl-worker.exe`。
- **问题**：非 Windows 平台（或 Python 解释器场景）永远回退 backend 自动探测，用户手动指定的 worker 无效（当前目标平台 win32，影响低）。
- **建议**：按 `sys.platform` 同时尝试无扩展名版本。

### [medium] runner/worker_task_runner.py:133-138 - `stop()` 失败仅记日志，状态卡 running
- **现状**：`except Exception: log_line("停止失败...")`，`_state` 保持 "running"。
- **问题**：与 run_controller.stop_task 叠加，GUI 永久停在"正在停止..."且按钮禁用。
- **建议**：失败时显式 `_set_state("error")` 或返回布尔并让 UI 回退。

### [medium] runner/worker_task_runner.py:143-149 - `_poll` 异常置 error 但不发 `task_finished`
- **现状**：连接中断时 `_poller.stop()` + `_set_state("error")`，不发 `task_finished`（其 `_current_task_id` 信息丢失）。
- **问题**：`task_finished` 信号在异常路径从未发射，依赖它的逻辑（如退出码处理）拿不到；当前仅靠 `state_changed("error")` 兜底。
- **建议**：异常路径也发 `task_finished(self._current_task_id, 1)`，统一完成语义。

### [low] runner/worker_task_runner.py:151-156 - 后端状态无 "status" 键时默认 running 永远轮询
- **现状**：`result.get("status", "running")`。
- **问题**：backend 返回结构变更（如 `{"ok": ...}`）时 poller 永不停止。
- **建议**：对结果做结构校验，异常结构按 error 处理。

### [low] runner/worker_task_runner.py:88-96 - `start()` 同步执行 `disk_usage`/`save_yaml`/`backend.start`
- **现状**：以上均在 GUI 线程。
- **问题**：通常毫秒级可接受；但大配置/慢盘下可能造成可感知卡顿。
- **建议**：必要时把 `validate`/`save` 移入一次性后台任务。

---

## 3) 交叉文件/一致性备注（非逐行、供参考）

- `run_controller.run_task` 与 `worker_task_runner.start` 各自独立调用 `validate_full_config`，重复校验（警告日志会打两遍）。
- `on_progress` 每次进度都 `setRange(0,100)`，多余；首次收到进度时设一次即可。
- 主线程的 `ToastManager.instance()` 在多个 delegate 中反复 `import` 是正常单例，但注意 `ToastManager` 未在窗口销毁时解绑 overlay（`sip.isdeleted` 兜底，OK）。
- `menu.py`/`toolbar.py` 的快捷键只显示在 tooltip，实际绑定依赖 `main.py:950 _setup_global_shortcuts`，若该函数遗漏任一快捷键，tooltip 与实际行为不一致（需在 main 层核对）。
- `closeEvent`（main.py:1786）负责收尾，本批文件中的 `QTimer`（elapsed、toast、resource monitor、poller）均挂在 QObject 父对象上，随窗口销毁，无需显式 join；worker 子进程为"独立常驻"设计（有意为之）。

---

## 附录：语法检查结果

```
python -m py_compile <全部 25 个文件>
PY_COMPILE_OK   # 全部通过，无语法错误
```

（命令输出于审查会话，无失败项。）
