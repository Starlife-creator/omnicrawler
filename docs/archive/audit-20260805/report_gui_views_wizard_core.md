# 审查报告: gui views/wizard/core

审查日期: 2026-08-05
审查人: 静态代码审查（只读审计，未修改任何文件）

## 汇总

### 审查范围

| 文件 | 实际行数 | 说明 |
| --- | ---: | --- |
| gui/core/autosave.py | 143 | 自动保存管理器（60s 定时器 + 草稿扫描） |
| gui/core/config_model.py | 205 | CrawlConfig / FieldDef / DownloadConfig 数据模型 |
| gui/core/config_serializer.py | 427 | to_yaml / from_yaml / save_yaml / load_yaml（ruamel） |
| gui/core/template_loader.py | 196 | 模板发现、加载、占位符提取 |
| gui/core/validator.py | 166 | schema 白名单 + 完整配置校验 |
| gui/core/__init__.py | 4 | 包导出 |
| gui/views/ai_service_center.py | 535 | AI 服务连接测试 / 模型列表（后台线程 + scoped 请求） |
| gui/views/change_monitor.py | 746 | 变更监控：规则 CRUD、定时/手动检查（后台线程） |
| gui/views/chart_view.py | 170 | CSV 图表视图（CsvLoadWorker 异步） |
| gui/views/developer_inspector.py | 42 | 开发检查器（EmptyState 占位） |
| gui/views/file_list.py | 97 | 下载文件列表 |
| gui/views/pdf_region_selector.py | 174 | PDF 区域点选对话框（PyMuPDF 截取文本） |
| gui/views/pdf_workbench.py | 725 | PDF 批量处理工作台（后台流水线线程） |
| gui/views/professional_review.py | 444 | 专业复核视图（字段/证据/置信度） |
| gui/views/result_table.py | 739 | 结果表格视图（异步索引 + 分页 + 证据查找） |
| gui/views/stealth_settings.py | 347 | 浏览器指纹伪装设置 |
| gui/views/task_history.py | 220 | 任务历史（JSONL） |
| gui/views/yaml_editor.py | 567 | YAML 编辑器与表单双向同步 |
| gui/views/__init__.py | 4 | 包导出 |
| gui/wizard/step1_source.py | 451 | 向导第 1 步：任务要点 + 自然语言解析 |
| gui/wizard/step2_urls.py | 367 | 向导第 2 步：URL、分页、增量 |
| gui/wizard/step3_fields.py | 842 | 向导第 3 步：字段表格 + 可视化选择 |
| gui/wizard/step4_download.py | 263 | 向导第 4 步：下载/筛选/PDF/AI |
| gui/wizard/step5_preview.py | 294 | 向导第 5 步：预览、试跑、保存 |
| gui/wizard/__init__.py | 4 | 包导出 |
| **合计** | **26 个 .py** | 23 个非 __init__ + 3 个包导出 |

PY_COMPILE_OK: 是（全部 26 个目标文件 `python -m py_compile` 通过）

### 问题分级计数

| 级别 | 数量 |
| --- | ---: |
| critical（崩溃/数据损坏） | 2 |
| high（功能失效/静默错误） | 7 |
| medium（边界/一致性） | 13 |
| ux（交互/反馈） | 9 |
| low（轻微/清理） | 5 |
| **合计** | **36** |

### 最重要问题速览（Top 10）

1. **[critical] pdf_workbench.py:515** — 处理运行中关闭窗口无 closeEvent 清理，QThread 随父窗口销毁 → Qt 崩溃（"QThread: Destroyed while thread is still running"）。
2. **[critical] change_monitor.py:585** — 检查线程并发无守卫 + `_on_check_finished` 无条件把 `_worker` 置 None，快速连点/定时触发会并发检查并互相覆盖状态。
3. **[high] step1_source.py:328（+ natural_language_task.py:75）** — 描述无 URL 时生成 `file:///placeholder` 占位 URL，经 `_apply_draft→initializePage` 写入配置且 validatePage 放行，产出坏任务。
4. **[high] step3_fields.py:767** — 高级可视化点选的 XPath 被当作 `selector_type="css"` 导入，字段无法匹配/测试必失败。
5. **[high] step3_fields.py:715** — 高级模式用模态框阻塞 + `get_selections()` 单次读取，未完成的选择在 `stop()` 后静默丢失。
6. **[high] result_table.py:632** — Markdown 导出在主线程全量读 CSV 并渲染，大文件 UI 冻结。
7. **[high] yaml_editor.py:389** — 编辑器同步校验只覆盖 project/source 子集，fields/crawl/download 等错误静默通过。
8. **[high] change_monitor.py:694** — `_save_rules` 直调 `_settings._set_value` 私有方法且无异常兜底。
9. **[high] step3_fields.py:779** — `_test_selector` 与 `SelectorTestThread` 只处理 css/xpath，jsonpath 类型永远返回"未匹配"。
10. **[medium] pdf_region.py:43** — `make_region_rule` 存 1 基页码而 `extract_region` 为 0 基，规则 page 语义不一致（当前无消费方，潜在整体偏移 1 页）。

## 问题清单

### [critical] pdf_workbench.py:515 - 运行中关闭窗口导致 QThread 随父窗口销毁而崩溃

现状：`_execute` 创建 `_PdfPipelineWorker(config_path, run_ocr=run_ocr, parent=self)` 并 start（:515-524）；`_cancel` 仅在用户点击"取消"时调用 `self._worker.cancel()`（:697-700）。文件内没有 `closeEvent`，主窗口/视图关闭时没有对运行中 worker 做 `requestInterruption`/`quit`/`wait`。

问题：PDF+OCR 是长任务，用户点击"开始处理"后直接关闭窗口/退出应用是常见操作；父对象销毁时运行中的 QThread 会被直接销毁，触发 Qt 致命错误。grep 全 views/wizard 均无 `def closeEvent`，此问题对 change_monitor、step3 `SelectorTestThread`、step5 运行链路同样存在。

建议：为 pdf_workbench（及同类长任务视图）实现 `closeEvent`，先 `self._worker.requestInterruption()` + `quit()` + `wait(3000)`，超时则 `deleteLater`；在运行期间禁用/重写关闭动作或弹出确认。

### [critical] change_monitor.py:585 - 检查线程并发无守卫，finished 回调无条件清引用造成竞态

现状：`_check_all`（:585-596）、`_check_single`（:758-768）都无条件创建新的 `_CheckWorker` 并 start；仅 `_periodic_check`（:600）检查 `self._worker is not None`。`_on_check_finished`（:627-628）无条件执行 `self._worker = None`。

问题：用户连点"全部检查"，或手动检查与定时检查重叠时，旧 worker 的 finished 会把 `_worker` 清空，导致新 worker 引用丢失、后续定时检查又叠加启动；多个并发检查同时写 `_rules_data` 和弹事件详情，产生重复通知与状态覆盖。全文件无 closeEvent，检查线程也会随窗口关闭被销毁。

建议：`_check_all`/`_check_single` 先 `if self._worker is not None and self._worker.isRunning(): return`；`_on_check_finished` 用 `self.sender() is self._worker` 判断再清引用；closeEvent 中断并 wait。

### [high] step1_source.py:328 - 自然语言描述无 URL 时占位符 `file:///placeholder` 写入配置并通过校验

现状：`_apply_natural_language`（:301-326）调用 `compile_natural_language(request, fallback_url=self._normalized_url())`。`natural_language_task.py:70-81` 在既无 URL 也无文件路径且 fallback 为空时返回 `task=draft_quick_task("file:///placeholder", "save_page")`；:87 纯文件模式同样用 `file:///placeholder`。step1 `_apply_draft`（:328-345）把 `draft.url` 写入 `config.seed_urls` 并调用 `initializePage()`（:345），初始化又把 URL 文本框设为 `file:///placeholder`（:212）。validatePage 的 `_is_supported_url`（:283-285）对 `file:///placeholder` 判定为合法（scheme=file 且 path 非空）。

问题：用户输入"帮我采集关于 XX 的新闻"（描述里无网址、URL 框为空）→ 得到一个 URL 为 `file:///placeholder` 的"合法"任务，保存/试跑后在运行期才报错，且占位符不含 `{{}}`，模板占位检测（validator:163、config_model:201）抓不到。

建议：step1 在 `_apply_draft` 前检查 `draft.url.startswith("file:///placeholder")` 则拒绝应用并提示补充网址；或让 `compile_natural_language` 对占位 URL 直接抛 `ValueError` 由 UI 引导用户输入。

### [high] step3_fields.py:767 - 高级可视化点选的 XPath 被当成 CSS 选择器导入

现状：`_apply_visual_candidates` 对每个候选构造 `FieldDef(name=..., selector=candidate.css, selector_type="css", fallback_xpath=candidate.xpath)`（:767-775）。高级模式（`_visual_pick_advanced` :749-755）把 `FieldConverter.merge_fields()` 的 `spec.get("selector")` 同时填进 `css` 和 `xpath`；而 `field_converter.py:61,75` 的 `_best_selector()` 返回的是 EasySpider 的 XPath（如 `//*[@id="main"]/...`）。

问题：XPath 字符串配上 `selector_type="css"` 后，`SelectorTestThread` 里 `CSSSelector(...)` 抛解析错误或匹配不到，提取链路同样失效；`fallback_xpath` 与 `selector` 相同也无法兜底。用户按"右键点选"生成字段后试跑为 0 命中。

建议：导入时统一 `selector=spec["selector"]`、`selector_type="xpath"`、`fallback_xpath=None`；`field_converter` 已提供 `to_omnicrawler_fields()`，直接复用其产物，避免手拼字段。

### [high] step3_fields.py:715 - 高级可视化模式单次读取选择结果，未完成选择静默丢失

现状：`_visual_pick_advanced`（:690-756）启动 `VisualSelectorServer` 后弹一个模态 `QMessageBox`（:715）等待用户去 Chrome 操作；用户点 OK 后立即 `get_selections()`（:724）并 `server.stop()`（:725）。

问题：模态框期间用户若仍在点选（OK 时最后一条 WebSocket 选择尚未被服务器线程处理），`stop()` 只 `join(timeout=3)` 后即返回，未处理完的选择被丢弃；且整个过程无"已收到 N 条"的进度反馈，选空时只给一次警告后没有任何重试入口。

建议：改为非阻塞等待：显示"等待选择…"状态条，轮询 `get_selections()` 并实时计数，用户确认后停止；`stop()` 前先 `clear_selections` 防陈旧数据，或对未消费选择给出确认。

### [high] result_table.py:632 - Markdown 导出在主线程全量读取 CSV

现状：`_export_markdown`（:619-644）在 GUI 线程直接调用 `MarkdownExporter.export_results`（:636-641）。`markdown_exporter.export_results` 会把整个 CSV 读入内存（逐行 append）并逐行拼接 Markdown。

问题：结果表面向大文件（已实现分页与异步索引），但 Markdown 导出仍是同步全量，几十万行时界面冻结数秒到分钟，且无进度反馈；文件写入失败前也无行数上限。

建议：复用 `ExportThread`（:258 已有 xlsx 导出线程模式）异步导出并显示进度；或先估算行数，超过阈值时提示"导出约 N 行，请稍候"并禁用按钮。

### [high] yaml_editor.py:389 - 编辑器同步校验仅覆盖 project/source 子集

现状：`_try_sync_from_editor`（:384-407）对 `from_yaml` 结果只构造 `{"project": {...}, "source": {...}}` 调 `validate_schema`（:389-392）。

问题：`validate_schema` 只检查 project/source/extract 三个段（validator.py:96-134），fields、crawl、download、ai、pagination 等段的非法值（例如非法选择器、缺失下载扩展名、非法 source_kind 之外的错误）全部静默通过，"已同步"提示给用户错误安全感。

建议：改为 `validate_full_config(config)`（validator.py:139）走 CrawlConfig.validate + 选择器格式 + schema 全量校验，或至少把整个配置 dict 传给 validate_schema。

### [high] change_monitor.py:694 - 规则持久化依赖设置对象私有方法且无异常兜底

现状：`_save_rules`（:694-707）在循环里把 datetime 转 isoformat 后调用 `self._settings._set_value("monitor/rules", serializable)`；`_save_monitor_settings`（:709-712）同样调用 `_set_value`。

问题：`_set_value` 是 `AppSettings` 的私有方法（名称前下划线），跨模块直调属于对实现细节的强耦合；若设置存储层重构或该键写入失败（磁盘/权限），`_save_rules` 无 try/except，会在检查结束回调里抛出并吞掉后续 UI 刷新。

建议：在 AppSettings 上提供公开 `set_monitor_rules(...)`/`monitor_rules()` 访问器；`_save_rules` 加 try/except，失败时 Toast 提示而非静默。

### [high] step3_fields.py:779 - 选择器测试对 jsonpath 类型永远"未匹配"

现状：`SelectorTestThread.run`（:386-437）只实现 css（CSSSelector）和 xpath 两个分支；`_test_selector`（:779-810）把表格中的类型下拉值直接传入，而下拉允许 `jsonpath`（:445 SELECTOR_TYPES）。

问题：选择 jsonpath 类型后测试，既不匹配也无明确报错，落入 `results = [(未匹配到任何内容,)]`（:430-431），误导用户以为选择器无效。

建议：实现 jsonpath 分支（按 key 提取），或对 jsonpath 类型明确提示"暂不支持测试"并在 UI 上禁用测试按钮。

### [medium] pdf_region.py:43 - PDF 区域页码 0/1 基不一致

现状：`extract_region(pdf, page_number, rect)` 要求 0 基页码（:26 检查 `0 <= page_number < page_count`）；`make_region_rule`（:35-43）在内部以 0 基调用后返回 `PdfRegionRule(..., page_number + 1, ...)`（:43，存 1 基）。`pdf_region_selector` 对话框把 QSpinBox 值 `-1` 后传入（0 基）用于生成规则，`_save_rule` 保存的 `page` 为 1 基并与 UI 显示一致。

问题：规则里的 `page` 字段语义是"用户可见的 1 基页码"，而 `extract_region` 的入参是 0 基；当前全仓库 grep 没有任何消费 `PdfRegionRule.page`/`pdf_region_fields` 的地方，属潜在缺陷——未来任一消费者拿 `rule.page` 直接喂给 `extract_region` 会整体偏移一页。

建议：在 `PdfRegionRule` 上明确字段语义（如改名 `page_1based` 并注释），或让 `make_region_rule` 接受 1 基并内部转换，消除两套约定。

### [medium] step2_urls.py:263 - 种子 URL 仅校验非空，任意文本均可进入后续流程

现状：`validatePage`（:263-283）只检查 URL 列表非空和占位符确认；step1 主页面 `_is_supported_url` 的格式校验不覆盖这里。

问题：用户粘贴 `mailto:xxx`、`abc`、`www` 等非法行也能通过，错误直到运行期才暴露；与 step1 的格式校验不一致。

建议：逐行复用 step1 的 `_is_supported_url`（或抽到共享工具函数）校验，非法行标红并定位到行。

### [medium] step2_urls.py:349 - 粘贴替换占位符时多余粘贴行被静默丢弃

现状：`_paste_from_clipboard`（:349-360）按占位符行与粘贴行一一配对替换，多出的粘贴行直接丢弃；无占位符时是整体追加。

问题：用户复制 5 行想替换 2 个占位符时，其余 3 行丢失且无提示，属于静默数据丢失。

建议：多余粘贴行提示用户"共 X 行超出占位符数量，已丢弃/已追加"。

### [medium] step3_fields.py:545 - 添加字段默认名在删除后重复

现状：`_add_field` 用 `f"field_{self._table.rowCount() + 1}"` 命名（:545）。删除若干行后 rowCount 变小，再次添加会产生与现存行同名的默认字段。

问题：`CrawlConfig.validate` 会报"字段名不能重复"（config_model.py:158），用户被未知的默认名冲突卡住。

建议：用递增计数器（持久到实例）或先检查现有字段名再生成唯一默认名。

### [medium] change_monitor.py:598 - 周期检查的到期判断对畸形时间戳/间隔处理过于宽松

现状：`_periodic_check`（:598-624）对 `last_checked` 用 `fromisoformat`，异常即 `due=True`；`interval` 取 `rule.get("check_interval", 3600)` 不校验类型。

问题：用户手改规则 YAML 或旧版本数据留下畸形 `last_checked`/字符串型 interval 时，每条规则每次 tick 都视为到期，频繁发起网络检查（且每次还有 H 级并发风险）。

建议：解析失败按"本次跳过并记录告警"处理而非直接 due；interval 统一转为数字并设下限（如 60 秒）。

### [medium] task_history.py:214 - workspace 路径来自 JSONL 且无存在性校验

现状：`view_results_requested.emit(workspace)`（:216）把 JSONL 记录里的 workspace 字符串直接发给主窗口用于打开结果。

问题：JSONL 可被篡改或手写，workspace 可能是任意路径；若打开逻辑对不存在目录只弹"未找到结果目录"而无路径合法性校验，存在 UX 误导（非注入，但需确认消费端）。

建议：消费端先 `Path(workspace).is_dir()` 校验，失败时提示具体路径而非泛化文案。

### [medium] result_table.py:101 - 模型层 load_file_async 不中断上一次索引线程

现状：`ResultTableModel.load_file_async`（:101-105）每次直接新建 `CsvIndexWorker`，旧 worker 只依赖 `_on_index_worker_finished` 用 `sender is self._index_worker` 防误清（:123-127）。

问题：视图层已在重载时中断旧 worker（widget :82-85），但模型公开入口不保证；连续打开两个文件时旧线程仍扫描旧文件空耗 IO，且窗口关闭时模型线程可能仍在运行。

建议：模型层在启动新 worker 前对旧 worker `requestInterruption`+`wait(1000)`。

### [medium] stealth_settings.py:290 - 指纹检查线程可重复启动

现状：`_check_fingerprint`（:290-300）每次无条件新建 `_FingerprintCheckWorker` 并 start，finished 接 `deleteLater`。

问题：连点"检查指纹"会并发多个网络指纹请求，结果按完成顺序覆盖，最后完成的未必是最新一次点击。

建议：启动前 `if self._fp_worker is not None and self._fp_worker.isRunning(): return`，或复用一个 worker。

### [medium] autosave.py:139 - 自动保存仅在距上次保存 ≥60s 时触发，关闭前无强制保存

现状：`_on_timer`（:139-142）判断 `time.time() - self._last_save_time >= AUTOSAVE_INTERVAL_MS/1000` 才保存。无 closeEvent 兜底。

问题：用户在两次保存间隔内编辑完并立即关闭，最后一次修改不会落盘；autosave 名义 60s 但实际取决于 timer 触发与退出时机。

建议：main 窗口 closeEvent 里调用一次强制 autosave（幂等：`_last_save_time` 直接判定为过期即可）。

### [medium] config_serializer.py:157 - AI 密钥引用明文写入 YAML 且格式不校验

现状：`to_yaml` 把 `config.ai_api_key_ref` 原样写进 `providers.<name>.api_key`（:157-158）；`from_yaml` 原样读回（:360）。GUI 只收集引用字符串（如 `secret://env/OMNICRAWL_AI_KEY`）。

问题：引用本身不是密钥，风险有限；但对引用格式无校验，用户可填任意字符串甚至直接粘贴明文密钥到配置文件，无任何告警。

建议：保存前校验 `secret://` 前缀，非引用格式弹提示；文档注明密钥通过环境/secret 引用。

### [medium] file_list.py:76 - 排序 stat 无异常处理

现状：`refresh` 用 `f.stat().st_mtime` 排序（:76），且 `iterdir()` 后直接遍历。

问题：下载目录并发被其他进程清理时，文件在 iterdir 与 stat 之间消失会抛 `OSError` 直接冒泡崩溃。

建议：排序用 `try/except OSError` 跳过失效条目。

### [medium] step3_fields.py:804 - SelectorTestThread 关闭页面后仍继续网络请求

现状：`_test_selector`（:804-810）创建线程并 start，线程内部有 `isInterruptionRequested` 检查（:388, 412），但无任何路径触发 requestInterruption；视图无 closeEvent。

问题：用户发起测试后立即关闭页面/向导，线程继续请求（最多 15s 超时），且 `result_ready` 回调会操作已销毁的对话框。短命但属泄漏路径。

建议：页面 closeEvent 中对 `_test_thread` `requestInterruption`+`wait(1000)`。

### [medium] chart_view.py:84 - CSV 抽样加载静默截断

现状：`CsvLoadWorker(path, sample_limit=sample_limit, parent=self)`（:84），sample_limit 默认 50_000（async_workers.py:34）。

问题：超过 5 万行的 CSV 只展示抽样，UI 无"仅显示前 5 万行抽样"提示，用户可能误以为是完整数据。

建议：finished_loading 已带 total_rows，把抽样状态写进信息栏。

### [ux] step1_source.py:301 - 自然语言解析失败仅文字提示

现状：`_apply_natural_language` 失败路径只 `set_error_style` + 文本反馈（:310-313），无 shake/焦点定位。

建议：与 validatePage 一致地 `shake_widget(self._task_description, self)` 并 setFocus。

### [ux] step1_source.py:257 - 未选输出格式时不定位到具体控件

现状：整组标红（:258-261），未指出是哪类缺失。

建议：逐个 `_output_checks` 标红未勾选项，或给出可点击定位的提示。

### [ux] change_monitor.py:653 - 多规则变化只展示第一条详情

现状：`_on_check_finished` 只对 `events_list[0]` 弹详情（:654-655）。

建议：给出"检测到 N 个变化"后弹出汇总对话框，可逐条查看。

### [ux] yaml_editor.py:405 - YAML 解析/校验错误无行号定位

现状：解析异常只整体变红 + 状态栏文本（:405-407, 427-439）；PyYAML/ruamel 异常自带 `line/column` 信息未使用。

建议：解析失败时把光标定位到出错行、行号高亮，错误信息拼接"第 N 行 第 M 列"。

### [ux] step4_download.py:235 - 扩展名去掉 pdf 后旧勾选状态残留

现状：`_update_enabled_state` 仅在扩展名不含 pdf 时禁用 `_process_pdf`/`_ocr`（:235-237），不取消勾选；`_save_to_config` 仍按 `isChecked()` 保存（:206-207）。

建议：扩展名变化且不含 pdf 时联动取消 `_process_pdf` 勾选，保持界面状态与配置一致。

### [ux] step5_preview.py:96 - 返回本页会覆盖试跑结果摘要

现状：`initializePage` 每次调用 `refresh_preview`（:96-97）重写 `_summary`，试跑后的 `show_sample_result`（:124）内容被清掉。

建议：把"试跑结果"与"配置摘要"分两个区域，或记录试跑状态，返回时保留结果。

### [ux] step2_urls.py:316 - 占位符仅文字提示，无跳转/替换引导

现状：`_check_placeholders` 只显示一行警告 + 高亮（:316-323），替换只能靠"粘贴"按钮的行配对。

建议：占位符警告提供"打开模板/粘贴并替换"快捷按钮。

### [ux] pdf_workbench.py:515 - 长任务无百分比进度

现状：进度条只接 `stage_started/stage_finished`（:518-522），没有 0-100% 进度值。

建议：流水线回调已具备阶段细分，映射为百分比或预计剩余文件数。

### [ux] professional_review.py:421 - 导出固定 card 样式

现状：`_export_markdown` 固定 `style="card"`（:439-443）。

建议：下拉选择 card/table/list，并预览导出文件。

### [low] step5_preview.py:269 - 用 `_()` 包裹已格式化字符串，翻译永久失效

现状：`_(f"Config saved to: {filepath}")`（:269、:286）把格式化后的字符串交给翻译函数，查表必不命中。

建议：改为 `_("Config saved to: {path}").format(path=filepath)`。

### [low] step3_fields.py:396 - UA 重复传入

现状：`scoped_fetch` 同时传 `headers={"User-Agent": ...}`（:396-399）与 `user_agent=user_agent("selector test")`（:399），后者被前者覆盖，冗余且易混淆。

建议：保留一个来源（推荐 `user_agent=` 参数）。

### [low] developer_inspector.py:44 - 预留方法空实现

现状：`update_config` 直接 `pass`（:44-46）。

建议：标注 TODO 并给出类型化签名，避免被误认为已完成功能。

### [low] config_model.py:201 - has_placeholders 未覆盖全部字段

现状：只检查 `seed_urls` 与 `field.selector`（:204-209），pagination、topic、ai_base_url 等含 `{{}}` 不检测。

建议：扩展到 pagination 参数等其余字符串字段，或抽公共扫描函数。

### [low] autosave.py:96 - 草稿扫描顺序不稳定

现状：`check_for_drafts` 直接 `iterdir()` 遍历（:96-103），顺序依赖文件系统。

建议：按修改时间排序，保证"恢复草稿"列表稳定展示。
