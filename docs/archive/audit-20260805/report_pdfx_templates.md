# 审查报告: pdfx/templates

审查范围：`src/omnicrawler/pdfx/`（22 个文件）与 `src/omnicrawler/templates/`（7 个文件），并交叉核对 `gui/views/pdf_workbench.py`、`pipeline_ops/pdf_integration.py` 等调用方。

审查方式：逐行 Read + `python -m py_compile` 全量语法检查（结果：**PY_COMPILE_OK，全部 30 个文件语法通过**）。

## 汇总

| 级别 | 数量 |
| --- | --- |
| critical | 0 |
| high | 7 |
| medium | 24 |
| low | 14 |
| ux | 6 |
| 合计 | 51 |

严重问题集中领域：OCR 多进程路径的降级缺口、LLM 客户端构造失败导致整阶段硬失败、Tesseract 语言参数不兼容、parse 阶段长事务持锁引发并发 BUSY、GUI 对失败与导出结构的误读、同路径同大小文件的去重数据陈旧。

---

## 问题清单

### [high] src/omnicrawler/pdfx/ocr.py:244-253, 355-359 - 多进程 OCR 无依赖预检，初始化失败导致整条管线崩溃

- 现状：`ocr_stage` 在 `workers > 1` 时直接进入 `ProcessPoolExecutor(initializer=_ocr_worker_init, ...)`。`_ocr_worker_init` 内 `PaddleStructureBackend/TesseractBackend` 构造失败（缺依赖、GPU 不可用、tesseract_cmd 无效）会抛出 `RuntimeError`，由进程池 initializer 抛出 → `BrokenProcessPool` 或任务全部失败。
- 问题：串行路径（`workers<=1`，ocr.py:326-341）有 D13 的优雅降级（记录 errors、标记 failed、返回 skipped），但多进程路径完全没有预检或降级；且 `service.run_processing`（service.py:107）未包裹 `ocr_stage`，异常直接冒泡 → CLI/GUI 整条 `run`/`run_extraction` 失败，用户看到的是“OCR 进程崩溃”而不是“依赖缺失，请安装”。
- 建议：进入多进程前先在本进程调用一次 `create_backend(config)` 预检（仅验证构造不识别）；在 `ProcessPoolExecutor` 外层包裹 `try/except (BrokenProcessPool, Exception)`，按 D13 语义标记 `skipped` 并写 errors。

### [high] src/omnicrawler/pdfx/extraction.py:317 - LLM 客户端构造失败使整个 extract 阶段硬失败，无规则降级

- 现状：`extraction_stage` 在提交任务前调用 `create_llm_client(config)`（extraction.py:317）。`OpenAICompatibleClient.__init__`（llm.py:109-125）在 API Key 为空、model 为空、base_url 非法或参数越界时直接 `raise ValueError`。
- 问题：`validate_runtime_config`（config.py:226）对“Key 为空”只发 warning 不拦截，而 `extraction_stage` 一旦构造失败会整体中断——本应执行的规则抽取完全没跑。D17 只覆盖了 LLM *请求* 失败（extract_document 内部 try/except），没覆盖客户端创建。CLI `run` 直接 exit 1；service.run_extraction 则把 extract 标 failed 并跳过 export，即使用户只想用规则。
- 建议：在 `extraction_stage` 中对 `create_llm_client` 包一层 try：失败时记 warning、`client=None`、以纯规则模式继续（与 D17 语义一致）。

### [high] src/omnicrawler/pdfx/ocr.py:128 - Tesseract 后端默认语言与内置模板不兼容，扫描件 OCR 全部失败

- 现状：`TesseractBackend.__init__` 用 `config.get("lang", "chi_sim+eng")` 取语言。但内置模板 `generic_template.yaml:17` 写死 `ocr: lang: ch`（Paddle 的语言名）。
- 问题：用户改用 `backend: tesseract` 而未改 lang 时，pytesseract 收到 `lang="ch"` → TesseractError，每个页面都失败且提示不友好（win32 上还不一定装了对应语言包）。GUI 只检测 `paddleocr` 存在性（pdf_workbench.py:441-455），对 tesseract 后端完全不设防。
- 建议：TesseractBackend 对非 `chi_sim/chi_tra/eng/...` 值做归一（`ch`→`chi_sim`），或 doctor/预检时校验 lang 是否可识别；GUI 的 OCR 可用性检测应按 `config.ocr.backend` 分支判断。

### [high] src/omnicrawler/pdfx/parser.py:199-234 + database.py:169 - 写事务横跨整个文档解析，并发 worker 长时间持锁

- 现状：`_parse_and_store` 在 `with db.transaction() as conn:`（即 `BEGIN IMMEDIATE`）内运行 `_iter_parsed_pages` 生成器，事务边界覆盖**整个文档**的逐页 `get_text`/`find_tables`/表格提取，最后才批量 `executemany`。`parse_stage` 用 `ThreadPoolExecutor` 并发（parser.py:148），每个 worker 独立连接。
- 问题：`BEGIN IMMEDIATE` 是排它写锁，一个大 PDF（尤其每页 `find_tables`）可能持锁数秒到数十秒；其余 worker 的 `BEGIN IMMEDIATE` 只能靠 `busy_timeout=60000` 等，超过即 `SQLITE_BUSY` → 该文档被判 parse 失败（D16 还会累加 attempt_count）。D41 注释声称“BEGIN IMMEDIATE + busy_timeout 保证并发写不冲突”，但前提是事务短小，这里不满足。
- 建议：改为“先流式解析到内存页列表（或临时分区表/分批事务提交），再开短事务 executemany”，或降低并发 + 提高 busy_timeout；至少把 `DELETE FROM pages` 与最终 INSERT 放同一个短事务，解析过程不持锁。

### [high] src/omnicrawler/pdfx/service.py:94-116 - 阶段隔离不完整 + 失败被 GUI 误报为“处理完成”

- 现状：`run_processing` 只对 ingest/parse 做了 try/except 隔离（service.py:94-101）；`ocr_stage`（107）与 `export_text_stage`（114）没有任何包裹，异常直接冒泡。而在异常被拦截时（ingest/parse），返回的 `results[stage]={"failed":True,...}` 且 `results["stopped"]=True`，`run_extraction` 直接把整个结果返回。
- 问题：① 同一函数内隔离策略不一致，OCR/文本导出失败会让整条 GUI 流程抛异常（走 failed 弹窗），而 ingest/parse 失败却“正常返回”；② GUI 的 `_on_done`（pdf_workbench.py:629-667）不检查 `processing.*.failed`，直接把结果当成功渲染“✓ 全部完成！”，toast 显示“共处理 0 份文档”——失败静默变成成功。
- 建议：service 层为 ocr/export_text 补上与 ingest/parse 相同的 try/except；GUI `_on_done` 检查所有 stage 的 failed/stopped 标志，有失败时走 `_on_failed` 或明确标注部分失败。

### [high] src/omnicrawler/pdfx/ingest.py:91-98 - path+size 去重跳过 SHA-256，同路径同大小文件更新后数据陈旧

- 现状：D45 优化用 `primary_path=? AND size_bytes=?` 命中即跳过哈希并直接复用旧 `doc_id`（ingest.py:91-95）。
- 问题：若用户用**同大小**的新版 PDF 覆盖原文件（替换合同/重新导出版本，字节数恰好相同很常见），会被当作 duplicate，旧 `sha256`、旧 `page_count`、旧解析结果全部保留；`document_sources` 却照常 upsert，产生“来源已更新但内容未重采”的静默错误。重新运行 parse/extract 时文档状态是 `parsed`，不会重解析。
- 建议：D45 至少记录 `mtime`/`sha256` 与快照比对，或命中后仍做一次低成本采样校验（前 4KB 哈希）；命中但 mtime 变化时强制全量哈希。

### [high] src/omnicrawler/pdfx/config.py:106 + normalization.py:211-239 - 类型白名单与规范化逻辑不一致，boolean/entity/relationship 成死代码

- 现状：`FieldSpec.from_dict` 的 `allowed_types`（config.py:106）只允许 `text/amount/currency/date/percent/integer/number/enum/code/year`；但 `normalization.py` 有完整的 `boolean`（211-217）、`entity`（238-239）分支，`extraction._shape_plausible`（extraction.py:92）也处理 `relationship`。
- 问题：配置 `type: boolean/entity/relationship` 会被 from_dict 直接拒绝（“type 不支持”），上述分支永远不可达；`entity` 类型还引用了 `normalization.entity_master_csv` 的整条链路（EntityResolver），全部悬空。此外 `code` 类型在 validation.py:59 被硬编码成“6 位数字”，与 value_pattern 灵活校验意图冲突。
- 建议：统一类型集合（要么把 boolean/entity/relationship 加进 allowed_types，要么删掉对应死代码），并给 `code` 的硬编码 6 位加配置开关。

---

### [medium] src/omnicrawler/pdfx/extraction.py:234-282 - 每条记录为全部字段写 NULL 行，长表数据膨胀

- 现状：`extract_document` 对 `field_map` 的每个字段都构造 `values[name]` 并 `INSERT INTO field_values`，即使该记录根本没抽到该字段（`raw_value=None`）。
- 问题：10 万记录 × 30 字段 = 300 万行，绝大多数是 6 列全 NULL 的空行；`field_values_long.csv` 与 Excel 长表会导出海量空行，`_wide_query` 的 `MAX(CASE...)` 也要扫这些空行。对比 `review.py:72` 的复核路径是“全空则跳过”，抽取路径却无条件落库，行为不一致。
- 建议：仅当 `raw_value/evidence` 至少一项非空时才 INSERT field_values；`required` 缺失校验仍由 validate_record 处理。

### [medium] src/omnicrawler/pdfx/extraction.py:333-334 + parser.py:148-152 - 一次性提交全部文档 Future，十万级内存峰值

- 现状：`parse_stage` 与 `extraction_stage` 都用 `{pool.submit(...): row for row in rows}` 提交全部任务。
- 问题：OCR 路径已按 500 分批（ocr.py:361 D37），但 parse/extract 未分批；十万文档 = 十万个 Future + 任务字典常驻内存，且取消后 `future.cancel()` 只对排队任务有效。
- 建议：仿照 ocr 的批次循环（提交一批 → as_completed → 下一批），控制常驻 Future 数量。

### [medium] src/omnicrawler/pdfx/ocr.py:398-405, 451-458 - OCR 逐页 UPDATE+独立 commit，写库性能差

- 现状：OCR 串行/多进程路径每页一次 `db.execute(UPDATE pages ...)`，每次调用都 `commit()`（database.py:185）。
- 问题：10 万页 = 10 万次事务提交；parse 已用 executemany 批量（parser.py:211-233），OCR 未复用该模式。
- 建议：按文档或按 500 页一批收集更新，在短事务内 executemany 后统一提交。

### [medium] src/omnicrawler/pdfx/ocr.py:416-430, 468-481 - 每文档 3 条 SQL 更新状态，N 文档 3N 查询

- 现状：OCR 完成后对每个受影响文档执行 2 次 COUNT + 1 次 UPDATE。
- 建议：改用一次 `GROUP BY doc_id` 汇总 pending/done 页数，再统一 UPDATE（或窗口函数一次性算 status）。

### [medium] src/omnicrawler/pdfx/ocr.py:224-241 - CPU 温度保护在 Windows 上无效

- 现状：`_check_temperature` 依赖 `psutil.sensors_temperatures()`，该方法在 win32 上不返回任何数据（或直接 NotImplemented）。
- 问题：平台是 win32；该“保护”在目标平台永远返回 True，注释宣称的行为在 Windows 不生效，且默认值 `_OCR_MAX_TEMP=85` 也无从校验。
- 建议：明确注释“仅 Linux/macOS 生效”，Windows 上改用可用的指标（如 GPU 温度/负载）或直接关闭；避免虚假的安全承诺。

### [medium] src/omnicrawler/pdfx/ocr.py:262-288 - worker 返回元组与类型注解不符，doc_id/completed 为死数据

- 现状：`_ocr_worker_process` 注解为 `tuple[str,int,str|None,float|None,int,float]`（6 元），实际返回 7 元 `(doc_id,page_no,text,confidence,printable,garbled,error)`；内部 `doc_id = os.path.basename(primary_path)`（271 行）计算结果从未被调用方使用（调用方用 futures 字典的 key）。`completed`（363/385/407）只自增从不读取。
- 建议：去掉未用 doc_id 返回与 basename 计算；修正注解为 7 元；删除 `completed`。

### [medium] src/omnicrawler/pdfx/parser.py:79-82 - parse_document 无任何调用方（死代码）

- 现状：`parse_document` 仅在定义处出现，全仓库无调用。
- 建议：删除或补单元测试并注明用途；其内部 `list(...)` 整文档收集与 D36 流式设计矛盾。

### [medium] src/omnicrawler/pdfx/desktop.py:11-23 - suite_root 无任何调用方（死代码）

- 现状：`suite_root` 全仓库无调用（`open_path` 被 services/workbench 使用）。
- 建议：删除或接入实际用途。

### [medium] src/omnicrawler/pdfx/parser.py:61 - 每页无条件执行 find_tables，大文档解析开销大

- 现状：非 OCR 页一律调用 `_extract_tables_markdown(page)` → `page.find_tables()`（parser.py:61, 92）。
- 问题：find_tables 对复杂版式开销不小，且持锁事务（见 high#4）内执行放大影响。
- 建议：加配置开关（如 `parser.table_detection: true`）或在 min_chars 极低、明显无表时跳过；至少放在持锁事务之外。

### [medium] src/omnicrawler/pdfx/parser.py:239-248 / llm.py:76-85 - 高 DPI 整页渲染无内存护栏

- 现状：`render_page` 用 `page.get_pixmap(dpi=dpi)`，config 允许 dpi 到 600（config.py:234）。600dpi A4 ≈ 4961×7016 px ≈ 100+MB/页；OCR 多进程下每 worker 再 ×2.5GB 内存估计（ocr.py:220）。
- 建议：渲染前按 `页宽×页高×dpi²` 预估像素数，超阈值时告警/限幅；或在配置校验中对大 dpi+大页面组合预警。

### [medium] src/omnicrawler/pdfx/review.py:23-34 - read_only 工作簿未关闭，文件句柄泄漏

- 现状：`_rows_from_xlsx` 用 `load_workbook(path, read_only=True, ...)`，返回后无 close。
- 问题：read_only 模式持打开的文件流，直到 GC；批处理复核大量 Excel 时句柄累积，Windows 下还可能在后续删除/覆盖文件时报权限错误。
- 建议：用 `with load_workbook(...) as wb:` 或 try/finally `workbook.close()`。

### [medium] src/omnicrawler/pdfx/validation.py:59-62 - value_pattern/无效正则未在配置期校验，运行时崩溃整个抽取事务

- 现状：`_type_format_issues` 里 `re.fullmatch(spec.value_pattern, ...)`；`value_pattern` 未在 `FieldSpec.from_dict` 编译校验。
- 问题：配置一个非法 `value_pattern`（如未闭合括号）→ 每个字段校验时抛 `re.error` → `validate_record` 抛错 → `extract_document` 事务整体失败 → 该文档被标记 `extract_failed`，且错误信息是裸 re.error，难以定位。
- 建议：from_dict 阶段用 `re.compile` 预校验 value_pattern（与 patterns 同机制），失败给出字段名提示。

### [medium] src/omnicrawler/pdfx/template_health.py:63-67 + template_monitor.py:58 - 快照文件损坏导致 observe 崩溃

- 现状：`StructureSnapshot.load` 直接 `json.loads`；`TemplateMonitor.observe` 读历史快照时不防损坏（save 又是非原子写 template_health.py:59-61）。
- 问题：中途断电/写一半的快照文件会让后续每次抓取在 observe 处抛 `JSONDecodeError/KeyError`，监测功能整个停摆。
- 建议：load 时捕获 JSONDecodeError/TypeError/KeyError 按“无历史”处理；save 改为原子写（复用 `atomic_write`）。

### [medium] src/omnicrawler/templates/template_monitor.py:86-92 - observations.jsonl 并发追加无锁、历史无界增长

- 现状：`history.open("a")` 直接写、`self.observations` 内存累积。
- 问题：多 worker 并发抓取时并发 append 可能交错；长期运行 jsonl 与内存列表无界增长。
- 建议：加线程锁（写文件与列表统一锁）、按天滚动或按 max_observations 截断。

### [medium] src/omnicrawler/templates/template_health.py:126-154 - TemplatePack 导入非事务性

- 现状：import_pack 校验全部通过后逐个 `write_bytes`（150-153），中途写失败（磁盘满/权限）会留下部分文件。
- 建议：先全部写入临时目录再整体 rename，或失败时回滚已写文件。

### [medium] src/omnicrawler/templates/apify_templates.py:149-215 - 生成模板 schema 疑似与当前 DEFAULTS 不一致

- 现状：生成的 YAML 用 `project/source/crawl/http/browser/extract/outputs` 顶层结构，其中 `extract.fields` 为 `{key:{selector,desc}}`、`crawl.same_host`、`browser.wait_until`、`http.respect_robots` 等。
- 问题：当前 `DEFAULTS`（core/config.py:88-91）的 `extract.mode` 为 `auto`、`fields` 为扁平 dict，且 DEFAULTS 中无 `same_host/wait_until/respect_robots` 键；生成物未被任何 validate 校验（`validate_project_template` 只校验 pdfx 模板），很可能被下游忽略或运行时报错。`field_list[:12]` 与 `desc` 文案也仅作“知识参考”。
- 建议：改用当前 schema 生成，或至少生成后跑一次校验并标注“需人工核对”；补充一个冒烟测试。

### [medium] src/omnicrawler/gui/views/pdf_workbench.py:656-660 - 导出文件结构 dict/list 不匹配

- 现状：`exporter.export_stage` 的 summary 中 `files` 是 dict（exporter.py:203-208，键 excel/results_csv/review_csv/long_csv），GUI `_on_done` 却 `for f in output_files` 按 list 迭代。
- 问题：界面显示的是 “📄 excel”“📄 results_csv” 等**键名**而非路径，用户无法知道输出在哪。
- 建议：GUI 改为 `export_info.get("files", {}).values()`，或 exporter 增补一个 list 形式字段（两者取一，勿并行）。

### [medium] src/omnicrawler/gui/views/pdf_workbench.py:113-116, 697-700 - 取消后 UI 卡死在 running 且密钥未清理

- 现状：worker.run() 在 `if self._cancelled: return`（113 行）直接返回，既不发 all_done 也不发 failed；`_cancel` 只调 `worker.cancel()`。
- 问题：取消后主界面停留在 “正在取消...”+ 进度条，按钮不可用，用户无法重新开始；同时 `_clear_injected_env`（D5 密钥残留防护）不会执行，`PDFX_LLM_API_KEY` 等残留在进程环境中。
- 建议：worker.run() 取消路径统一发 `all_done`（带 stopped 标志）或专用信号；`_cancel` 处等待线程结束并清理注入环境变量。

### [medium] src/omnicrawler/pdfx/__init__.py:3 - 触发 omnicrawler 全量兼容别名注册，首包导入沉重

- 现状：`from .. import __version__` 会执行 `omnicrawler/__init__.py` 的 `_setup_compat_aliases()`（omnicrawler/__init__.py:192），逐个 importlib 加载几十个兼容子模块（含 services.workbench 等再引 pdfx）。
- 问题：任何 `import omnicrawler.pdfx.xxx` 都会连锁加载大量模块，启动慢且存在循环导入隐患（pdfx 加载一半时被 services.workbench 反向引入）。
- 建议：pdfx/__init__ 直接内联版本号或 `importlib.metadata.version`，避免触发根包；`_setup_compat_aliases` 改惰性/按需注册。

### [medium] src/omnicrawler/pdfx/config.py:280-296 - 目录重叠校验不完整

- 现状：校验了 input vs work、input vs output、database vs input，但未校验 work 与 output 重叠、database 位于 output_dir 内。
- 问题：默认 database 在 work_dir 内（config.py:195），若用户把 output_dir 设为 work_dir，则导出 summary.json/text/CSV 会直接写入工作目录并与 sqlite 混放；也影响 atomic rename 语义。
- 建议：补 `overlaps(work_dir, output_dir)` 与 `overlaps(database, output_dir)` 校验。

### [medium] src/omnicrawler/pdfx/normalization.py:69-70 - “百万/百元”等单位缺失，金额换算错误

- 现状：`AMOUNT_UNITS` 无 “百万”“百元”；“1百万”会被按 “万” 命中 → 1×10^4 = 1 万元（实际应为 100 万元）。
- 建议：补充 “百万”:1_000_000（置于 “万” 前）与 “百元”:100，并对 “千万元” 等组合加回归测试。

### [medium] src/omnicrawler/pdfx/normalization.py:32-35 - 括号负数启发式误伤正数

- 现状：`[(（]\s*([\d,，.]+)\s*[)）]` 一旦匹配即按负数处理。
- 问题：正文中合法括注（如 “（元）(1)” “编号(100)”）会被解析成 -1/-100，金额翻转。
- 建议：仅当整段文本无其他数字信号/符合会计括号惯例时取负，或把该启发式收敛到金额字段且要求括号内容为纯数字格式。

### [medium] src/omnicrawler/pdfx/normalization.py:104-111 - percent 把无单位 “1” 判定为 100%

- 现状：`0 <= value <= 1` 且无 “%” 时 ×100，“1”→“100”。
- 问题：“1” 在多数表格语境是 1% 或 1 个百分点，被放大 100 倍；与 amount 的保守策略不一致。
- 建议：无单位且值恰为 1 时提示复核/置 NULL 或按可配置策略处理。

### [medium] src/omnicrawler/pdfx/retrieval.py:76-79 - fallback_pages 非法配置运行时抛 ValueError 中断抽取

- 现状：`{int(page) for page in fallback if int(page) in by_number}` 对非数字项直接 `int()` 抛错，且 config 未校验 fallback_pages 类型。
- 建议：配置期校验 fallback_pages 为整数列表；运行时对无法解析项跳过并 warning。

### [medium] src/omnicrawler/templates/template_catalog.py:160-168 - URL 正则匹配无超时/大小约束（ReDoS 面）

- 现状：`re.search(pattern, probe.url, re.IGNORECASE)` 直接对模板元数据正则执行，probe.url 来自抓取结果（不可信）。
- 问题：模板来自用户 pack 时，恶意/病态正则 + 长 URL 可能造成灾难性回溯卡死推荐流程。
- 建议：限制 pattern 长度并对用户包正则用 timeout（regex 模块）或 subprocess 隔离；至少限制 URL 长度。

### [medium] src/omnicrawler/templates/template_catalog.py:285 - min_core_version 默认值不一致

- 现状：dataclass 默认 `"0.0.1"`（template_catalog.py:43），`_read_record` 缺省时却填 `"1.0.0"`。
- 问题：同一语义两套默认，版本判断结果随路径不同。
- 建议：统一为同一常量。

### [medium] src/omnicrawler/pdfx/cli.py:110-113 - doctor/validate 先强校验配置，配置损坏时无法出诊断

- 现状：main 在分发命令前统一 `load_config`（110 行），doctor 也走这条线。
- 问题：doctor 本意是环境诊断，遇到坏配置却直接报 “错误: ValueError:...” 退出，用户得不到依赖/目录检查结果。
- 建议：doctor 对配置加载失败降级为 “配置不可加载” 项继续输出其余检查。

### [medium] src/omnicrawler/pdfx/service.py:61-66 - prepare_config 在阶段隔离之外

- 现状：`prepare_config`（load+validate+mkdir）在 try/except 之外，配置错误直接冒泡。
- 建议：将配置加载错误归入 results["error"]，与阶段隔离语义一致，GUI 可显示友好消息。

### [medium] src/omnicrawler/pipeline_ops/pdf_integration.py:69 - PDF 数量统计口径不一致

- 现状：`pdf_files = list(pdf_input.glob("*.pdf"))` 只统计顶层，而 pdfx `iter_pdfs` 用 rglob 递归。
- 问题：子目录含 PDF 时 result["documents"] 与实际上报/处理数不一致。
- 建议：与 pdfx 统一递归统计，或显式注明仅统计顶层。

### [low] src/omnicrawler/pdfx/exporter.py:41-61 - 宽表查询表达式随字段数线性膨胀

- 现状：每字段 6 个 `MAX(CASE...)`；字段 30+ 时 SQL 很长，且 `LEFT JOIN + GROUP BY` 扫全表 NULL 行（配合 medium：extraction 空行问题更严重）。
- 建议：先只查必填列或先按 record 预筛，再分块导出。

### [low] src/omnicrawler/pdfx/exporter.py:122-129 - 行截断标记只写不记

- 现状：Excel 行超 1048576 时追加 “[已截断...]” 行，但 summary 不标记 truncated。
- 建议：返回 `truncated=True` 并让 CLI/GUI 提示“完整数据见 CSV”。

### [low] src/omnicrawler/pdfx/exporter.py:110-118 - 数值以字符串写入 Excel 单元格

- 现状：CSV 回读后所有值都是 str，数字列在 Excel 显示为文本（绿色三角）。
- 建议：对 NUMERIC_TEXT 命中的字符串转 float/int 再写入。

### [low] src/omnicrawler/pdfx/llm.py:88-94 - 请求体大小估算重复计入 prompt 且精度差

- 现状：`estimated = len(prompt.encode()) + sum(len(str(part).encode()) for part in content)`，而 content 已含 prompt 文本（重复计入），`str(dict)` 长度与真实 UTF-8 JSON 字节不一致。
- 建议：直接对 `json.dumps(payload)` 编码量取 len，超限再降级图片。

### [low] src/omnicrawler/pdfx/llm.py:158 - 参数缩进错位

- 现状：`user_agent=user_agent("PDF LLM extraction"),` 缩进比同级参数少 2 空格（语法合法，可读性差）。
- 建议：对齐缩进。

### [low] src/omnicrawler/pdfx/llm.py:101,129-131 - base_url 尾斜杠端点拼接边界

- 现状：`endpoint` 判断 `endswith("/chat/completions")`；若 base_url 以 `.../chat/completions/` 结尾（含尾斜杠）会再拼一层。
- 建议：endpoint 计算前 rstrip("/")。

### [low] src/omnicrawler/pdfx/utils.py:46 - os.replace 无重试，Windows 目标被占用时导出失败

- 现状：`atomic_output_path` 一次 `os.replace`；若用户正用 Excel 打开结果文件，PermissionError 直接冒泡。
- 建议：仿照 `core/utils.atomic_write`（core/utils.py:101-108）的重试退避。

### [low] src/omnicrawler/pdfx/utils.py:106-124 - extract_json_object 的 rfind 花括号可能落在字符串内

- 现状：兜底用 `stripped.rfind("}")`，若 JSON 字符串值内含 `}`（如 `"reason":"}"`）会截断导致第二次解析失败。
- 建议：用带状态的括号配对扫描取最外层对象。

### [low] src/omnicrawler/pdfx/safe_regex.py:41,52 - 无 regex 库时静默失去超时保护

- 现状：`timeout_regex is None` 时直接用 `re` 执行（无超时），NESTED_QUANTIFIER 只能拦截一小部分回溯模式。
- 建议：回退路径明确告警或禁止启用自定义正则；把 regex 列为硬依赖。

### [low] src/omnicrawler/pdfx/safe_regex.py:16 - 嵌套量词启发式有限

- 现状：`NESTED_QUANTIFIER` 只能识别 `(...)`+量词形态，`(a|a)*`、`(?:ab){2,}` 组合等复杂回溯模式不拦截。
- 建议：依赖 regex 超时兜底 + 增加模式覆盖，而非当作完备防护。

### [low] src/omnicrawler/pdfx/config.py:233-278 - 运行时校验的 int()/float() 转换错误信息不友好

- 现状：`int(config.ocr.get("dpi", 220))` 等对非法值抛 `ValueError: invalid literal for int()...`，且 `include_page_images` 未做布尔类型校验（字符串 "false" 也为真）。
- 建议：统一捕获并转为中文配置错误；布尔项显式校验。

### [low] src/omnicrawler/pdfx/database.py:132 - 用 assert 校验 PRAGMA

- 现状：`assert ... == 1`，`python -O` 下断言被剥离，静默失效。
- 建议：改为显式 raise。

### [low] src/omnicrawler/pdfx/database.py:194-206 - reset parse 不恢复 parse_dead

- 现状：reset 阶段用 `status NOT IN ('invalid','needs_password')` 还原，parse_dead 不在恢复范围。
- 问题：用户修复损坏 PDF 后无法通过 reset 重试，只能重新 ingest。
- 建议：reset 增加恢复 parse_dead 选项或在文档说明。

### [low] src/omnicrawler/pdfx/extraction.py:352-354 - summary 类型注解与实际返回不符

- 现状：注解 `dict[str, int]`，实际含 `"stopped": True`（bool）与 `"extraction_methods": dict`。
- 建议：改为 `dict[str, Any]`。

### [low] src/omnicrawler/pdfx/extraction.py:237 - required_names 在循环内重复计算

- 现状：`required_names = {spec.name ...}` 位于每记录循环内。
- 建议：提到循环外。

### [low] src/omnicrawler/pdfx/extraction.py:145 / retrieval.py:89 - 小问题集合

- 现状：`_observable_confidence` 的 `str(page_no).isdigit()` 防御可读性差；`retrieval.py:89` 循环内 `import json`。
- 建议：前者显式 None 判断，后者移到模块顶部。

### [low] src/omnicrawler/pdfx/normalization.py:78-101 - 纯月份/纯数字日期格式解析不全

- 现状：“2023-12”（无日）→ pattern1/2 不命中 → pattern3 输出 “2023”，丢掉月份；“2024.5” 同理。
- 建议：补充 `yyyy[-./]mm` 无日模式。

### [low] src/omnicrawler/pdfx/normalization.py:119-145 - 实体表模块级缓存不感知文件变更

- 现状：`_entity_cache` 按路径缓存后永不过期，同进程内 CSV 被修改不生效。
- 建议：带 mtime 缓存键。

### [low] src/omnicrawler/pdfx/config.py:155-160, 180-191 / normalization.py:164-177 - 项目根推断逻辑重复两处

- 现状：`_pdf_project_root` 与 load_config 的 base 解析、normalization.from_config 三处逻辑几乎一致。
- 建议：抽取公共函数。

### [low] src/omnicrawler/templates/template_catalog.py:100-102 - get() 多匹配取第一个

- 现状：按 stem/name 匹配多个时静默取第一个。
- 建议：歧义时返回 None 或记录冲突。

### [low] src/omnicrawler/templates/template_health.py:143 - 导入校验对非 UTF-8 payload 抛裸 UnicodeDecodeError

- 现状：`yaml.safe_load(payload.decode("utf-8"))` 解码失败时无上下文错误。
- 建议：捕获并给出文件名与原因。

### [low] src/omnicrawler/templates/template_health.py:116-123 - 导出包内文件名未校验 Windows 非法字符

- 现状：`templates/{template_id}.yaml`，template_id 若含 `:`/`?` 等，Windows 上导入写盘失败。
- 建议：export 前按 safe_filename 归一或拒绝。

### [low] src/omnicrawler/pdfx/text_export.py:71-73 - 同名文档依赖 doc_id 前 8 位区分

- 现状：不同目录同名文件 → 同 stem，靠 sha256 前 8 位（32 位空间）区分。
- 建议：用完整 doc_id 或冲突时自动后缀。

### [low] src/omnicrawler/pdfx/cli.py:40-46 - doctor 不检查 safe_regex 依赖的 regex 库

- 现状：doctor 只查 fitz/yaml/openpyxl + OCR 后端依赖。
- 建议：补充 `regex` 检查（超时保护依赖它）。

### [ux] src/omnicrawler/gui/views/pdf_workbench.py:667 - 完成 toast 用 `docs.get('ingested')`

- 现状：toast “共处理 {docs.get('ingested','?')} 份文档”，而文档状态字典键多为 `parsed/extracted` 等，运行后通常显示 “0 份”。
- 建议：用 pipeline 各阶段计数（new/parsed/extracted 求和）替代。

### [ux] src/omnicrawler/pdfx/cli.py:161-163 - 顶层 except 不打印调用栈且不提示如何排查

- 现状：只打印 `类型: 消息`。
- 建议：加 `--debug` 时输出 traceback，并提示 `pdf-core doctor`。

### [ux] src/omnicrawler/pdfx/ocr.py:326-341 - OCR 后端缺失时串行路径提示可懂，但 CLI 不展示

- 现状：日志 `logger.error` 记录降级，CLI 仅打印 JSON summary（skipped=N），用户看不到“为何跳过”。
- 建议：emit 阶段结果中带 `reason` 字段，GUI/CLI 展示。

### [ux] src/omnicrawler/templates/template_health.py:99-103 - validate_catalog 无汇总/可读输出

- 现状：返回 TemplateHealth 列表，无 ok/warning 计数；若 CLI 直接打印对象，可读性差。
- 建议：提供 `summary()`（ok/errors/warnings 计数 + 前 N 条）或 CLI 友好渲染。

### [ux] src/omnicrawler/pdfx/exporter.py:181-189 - Excel 生成对超大 CSV 无进度反馈

- 现状：write-only workbook 逐行读 CSV 生成，10 万行级无进度，GUI 停在 “导出 Excel/CSV”。
- 建议：导出阶段支持进度回调或 CLI 打印行数递增。

### [ux] src/omnicrawler/gui/views/pdf_workbench.py:547 - “预计文本量”用文件字节数冒充字符数

- 现状：`char_count = sum(path.stat().st_size ...)`，中文 PDF 字节≈3 倍字符。
- 建议：按文档估算页数×字符或明确标注 “约 X MB”。

### [ux] src/omnicrawler/pdfx/service.py:82 - 警告只在 `_emit` 时投递一次

- 现状：`validate_runtime_config` 在 prepare_config 与 run_processing 各调一次，GUI 只收到 `warnings` 事件一次但重复计算。
- 建议：去重或统一一次。

---

## 附：语法检查结果

`python -m py_compile` 对 pdfx(22) + templates(7) + `__main__.py` 全部 30 个文件：**通过（PY_COMPILE_OK）**，无语法错误。
