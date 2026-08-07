# 审查报告: pipeline/runtime/scheduling/state

- 审查时间：2026-08-05
- 审查范围：37 个文件，约 5,211 行（逐行人工审查 + `python -m py_compile` 全量语法检查，全部通过，无编译失败）
- 审查方式：纯审查，未修改任何文件

## 汇总（按严重级别计数）

| 严重级别 | 数量 |
|---|---|
| critical | 0 |
| high | 4 |
| medium | 20 |
| low | 28 |
| ux | 4 |
| **合计** | **56** |

关键结论（提前摘要）：
1. `reprocess_records` 重处理记录后调用 `_run_exports`，因导出幂等提交已 `succeeded` 会被直接跳过 → **重处理结果不会重新导出，输出文件陈旧**（high）。
2. `AutoPilot` 的磁盘保护（`run_state→pause`）提案在应用循环中被静默丢弃，且 `maybe_adjust` 只由 error/latency 趋势门控 → **磁盘/OCR/DOM 保护机制实际不生效**（high）。
3. `RedisFrontier.push` 用非事务 pipeline 写入 `seen`/`queue`，且 `len(list(requests))` 会消费生成器 → **分布式模式可丢任务、`added` 计数恒为 0**（high）。
4. `StateStore.claim` 的 SELECT→UPDATE 非原子，WAL 下多进程可双重认领同一批请求（high，单进程模式风险低）。
5. 语法检查：37 个文件全部 `py_compile` 通过，无编译错误。

---

## 问题清单

### pipeline 子包

#### [high] src\omnicrawl\pipeline\_run.py:363 - reprocess_records 重新导出被幂等提交跳过，输出文件陈旧
- 现状: `reprocess_records` 先 `reset_record_stage(run_id)` 删除记录并重新提取，最后调用 `self._run_exports(run_id)`；`_run_exports` 对每个导出器先 `begin_export(run_id, name, f"{run_id}:export:{name}")`。
- 问题: 原 run 若已成功导出，`export_commits` 中该 idempotency_key 状态为 `succeeded`，`begin_export` 返回 False 且 `export_commit` 直接复用旧结果（见 `_exports.py:38-43`）→ 导出器**不会重新运行**，JSONL/CSV/XLSX/DuckDB 等文件仍是旧内容，但 `reprocess_records` 返回 `"status": "succeeded"` 并附上陈旧 `export` 结果。用户会误以为重处理已生效。常见场景（原 run 完整成功后再重处理）必然触发。
- 建议: reprocess 使用新的导出 idempotency key（如 `f"{run_id}:reprocess:{epoch}"`）或在 `reset_record_stage` 时清除对应 `export_commits`；并明确在摘要中标注“本次导出由 reprocess 生成”。

#### [high] src\omnicrawl\runtime\auto_pilot.py:253-274 - 磁盘保护（run_state→pause）提案被丢弃且触发门控过窄
- 现状: `AdaptiveController.propose` 在磁盘低于阈值时产出 `Adjustment("run_state", "running", "pause", ...)`（`adaptive_execution.py:54-55`）。`AutoPilot.maybe_adjust` 先由 `should_adjust`（仅 error/latency 趋势）门控，再在应用循环里只处理 `concurrency`/`wait_seconds`/`ocr` 三种参数。
- 问题: ① `run_state` 提案在 `for adj in proposals` 中被静默忽略，磁盘保护永远不触发；② 即使 DOM 稳定性差（应加大 wait）、磁盘低、文本层质量充分（应关 OCR），只要 error/latency 趋势不达标，`maybe_adjust` 直接返回 `[]`，其余提案全部不生效。控制器文档宣称的“自动调整 OCR、暂停运行”实际是死功能。
- 建议: 应用循环补上 `run_state` 分支（通过暂停标志/回调）；`should_adjust` 门控应纳入 dom_stability、free_disk 等条件，或让 `maybe_adjust` 不提前短路。

#### [high] src\omnicrawl\runtime\redis_frontier.py:23-42 - push 非事务批量写入 + 生成器消费导致计数错误与丢任务
- 现状: `push` 用 `self.client.pipeline(transaction=False)`，逐请求执行 `sadd(seen)` + `zadd(queue)`；末尾 `for i in range(len(list(requests)))` 统计新增。
- 问题: ① `transaction=False` 非原子，worker 中途崩溃/断连会留下“已 seen 但未入队”的请求 → 分布式模式下**任务丢失且无恢复**；② `requests` 为生成器/迭代器时，首个 `for request in requests` 已将其消费完，`len(list(requests))` 返回 0 → `added` 恒为 0（对 list 则多一次 O(n) 拷贝）；③ `added` 语义实际是“seen 集新增数”而非“入队数”，与函数语义不符。
- 建议: 用 `MULTI/EXEC`（`transaction=True`）或 Lua 脚本保证 `seen`+`queue` 原子；先 `requests = list(requests)` 再 `enumerate`；返回值明确定义为入队数。

#### [high] src\omnicrawl\runtime\redis_frontier.py:21,35 - 队列成员用完整 payload 而非 fingerprint 去重 + seen 集无限增长
- 现状: `seen` 集合成员是 `request.fingerprint`，而 `zadd(queue, {payload: -priority})` 的成员是完整 JSON payload（含 headers/meta）。
- 问题: ① 同一 fingerprint 的请求若 meta/headers 有差异，payload 字符串不同 → zset 中出现多个成员，`pop` 会重复抓取同一 URL（去重不一致）；② `seen` 集永不设 `expire`，长期运行无限增长。
- 建议: 队列成员改用 `request.fingerprint`，payload 作为 score 附带字段或用 hash 存储；对 `seen` 设置过期策略或清理任务。

#### [medium] src\omnicrawl\pipeline\_run.py:239 - 资源超限（status="failed"）后仍执行 PDF 阶段
- 现状: 主循环捕获 `ResourceLimitError` 后 `status = "failed"`、`drain()`、break，随后 `if pdf_enabled and status != "cancelled":` 执行 `run_pdf_pipeline`。
- 问题: 因磁盘/运行时长超限而失败的运行，紧接着又跑重量级 PDF 提取/OCR 阶段，可能再次触发资源超限或拖慢收尾，与“资源保护”目标相悖。
- 建议: 仅 `status == "succeeded"`（或显式配置）才执行 PDF 阶段，失败/取消一律跳过。

#### [medium] src\omnicrawl\pipeline\_run.py:131-136 - 每个 URL 写入一条 stage_checkpoint，表无限增长
- 现状: `consume()` 对每个成功请求执行 `save_checkpoint(run_id, "fetch", request.fingerprint, ...)`，主键 `(run_id, stage, idempotency_key)`。
- 问题: 每抓一个 URL 就多一行（且是一次额外写事务），百万级 URL 时 `stage_checkpoints` 无界膨胀；恢复时也没有使用这些行做断点续抓，成本与收益不符。
- 建议: 检查点按阶段粒度覆盖写（key 固定为 `"crawl"`），URL 级进度已由 `frontier.status` 表达，无需逐 URL 落库。

#### [medium] src\omnicrawl\pipeline\_run.py:175,137 - max_pages 只统计成功页，实际请求数可超预算
- 现状: `processed` 仅在成功消费后自增（`consume` 内），循环条件 `processed < limit`；blocked/failed 均不计数。
- 问题: 若站点大量链接被策略拦截或抓取失败，循环会持续认领直到 frontier 耗尽，实际发出的 HTTP 请求数可远超 `max_pages`（预算失控）。
- 建议: 将“已尝试”计数并入限额，或明确文档说明 `max_pages` 指成功页并同时提供硬性尝试上限。

#### [medium] src\omnicrawl\pipeline\_run.py:95 - max_pages=0 被当作未传
- 现状: `limit = max_pages or int(crawl.get("max_pages", 100))`。
- 问题: 显式传 `max_pages=0` 会回落到配置默认值（100），无法表达“0 页”。
- 建议: 改为 `limit = int(crawl.get("max_pages", 100)) if max_pages is None else max_pages`。

#### [medium] src\omnicrawl\pipeline\exporters.py:150-153 - Parquet 导出把所有值强转 str，丢失类型
- 现状: `safe_records = [{key: None if value is None else str(value) ...}]` 后 `pa.Table.from_pylist`。
- 问题: 数字、日期、布尔全变字符串，Parquet 面向分析的价值（schema/类型推断/谓词下推）被破坏。
- 建议: 让 pyarrow 自动推断类型，仅对真正无法推断的值做 `str`；或按字段类型显式构建 schema。

#### [medium] src\omnicrawl\pipeline\exporters.py:66-70,44-51 - 扁平化键与基础字段同名时互相覆盖
- 现状: `flat` 先写入 `record_id/source_url/record_type/created_at`，再 `_flatten("", json.loads(row["data_json"]), flat)` 递归写入 data 键。
- 问题: 页面数据若含 `record_id`、`source_url` 等同名字段，会静默覆盖基础字段，导致 CSV/XLSX/DuckDB 列被污染（且无前缀隔离）。
- 建议: 数据字段统一加前缀（如 `data.`）或对基础字段名做保留检查并在冲突时重命名。

#### [medium] src\omnicrawl\pipeline\exporters.py:112-118 - openpyxl 缺失时静默跳过 xlsx 导出
- 现状: `except ImportError: pass`（`outputs.get("xlsx", True)` 默认开启）。
- 问题: 与 parquet 分支（会追加 `optional_warnings`）不一致，用户开启默认 xlsx 却拿不到文件且无任何提示。
- 建议: 补上与 parquet 相同的 `optional_warnings.append(...)`。

#### [low] src\omnicrawl\pipeline\exporters.py:80 - JSONL 行同时包含 data_json 与嵌套 data/evidence 冗余键
- 现状: `json.dumps({**row, "data": json.loads(row["data_json"]), "evidence": json.loads(row["evidence_json"])})`。
- 问题: `row` 本身已含 `data_json`/`evidence_json` 原始列，输出行内键重复冗余，消费方容易用错键。
- 建议: 去掉 `row` 中的 `*_json` 原始键或删去内联 `data`/`evidence`，二者只保留其一。

#### [low] src\omnicrawl\pipeline\exporters.py:87 - 摘要统计假设 endpoints 可 int() 化
- 现状: `sum(int(item.get("endpoints", 0)) for item in self._api_discoveries)`（`_exports.py:87`）。
- 问题: 若插件写入非数字 `endpoints`（如字符串），整次导出在汇总阶段抛 ValueError，且无兜底。
- 建议: 用 `isinstance(v, (int, float))` 过滤后再求和。

#### [low] src\omnicrawl\pipeline\_exports.py:95 - 假设 record_sinks.status() 恒含 recent_errors 键
- 现状: `summary["storage_warnings"] = summary["storage"]["recent_errors"]`。
- 问题: 若某 sink 状态不含该键，导出阶段 KeyError 使整个 run 失败，错误信息指向内部键名，不友好。
- 建议: 用 `.get("recent_errors", [])`。

#### [low] src\omnicrawl\pipeline\_exports.py:43 - 未完成导出冲突的报错缺少导出器名
- 现状: `raise RuntimeError("导出器{name}已有未完成提交，拒绝重复提交")` —— 实际占位符未格式化（字符串直接写 `{name}`，非 f-string）。
- 问题: 报错文本字面输出 `导出器{name}已有...`，无法定位是哪个导出器；属于 UX 缺陷。
- 建议: 改为 f-string 并附上 `idempotency_key`。

#### [low] src\omnicrawl\pipeline\_extract.py:109 - transform_record 返回 None 会使下游 AttributeError
- 现状: `outcome.records = [transform_record(transformer, record) for record in outcome.records]`。
- 问题: 若 transform 插件以返回 `None` 表示“丢弃该记录”，records 中出现 None，随后 `enrich_records`/`save_records`（`record.source_url`）会崩溃；契约未明确。
- 建议: 明确 transform 契约（丢弃用抛异常或 `record.keep=False`），并在此过滤 None 并记录日志。

#### [low] src\omnicrawl\pipeline\_extract.py:184-185 - quality_threshold / unique_by 的类型假设脆弱
- 现状: `float(extract_config.get("quality_threshold", 0.8))`；`[str(item) for item in extract_config.get("unique_by", [])]`。
- 问题: 阈值配成非数字字符串（如 "auto"）直接 ValueError；`unique_by` 配成字符串时逐字符迭代，配成 None 时 TypeError。
- 建议: 对配置值做类型校验并给出可读错误。

#### [low] src\omnicrawl\pipeline\_fetch.py:27-37 - 线程本地 fetcher 永不关闭（资源泄漏）
- 现状: 非 browser 的 fetcher 存于 `threading.local().fetchers`，`Pipeline.close()` 只关闭 `_shared_fetchers`（browser）。
- 问题: http/httpx_async 客户端持有的连接池、会话句柄随线程池常驻而泄漏，进程存活期内不释放。
- 建议: 在 `close()` 中遍历活跃线程本地存储关闭 fetcher，或为 fetcher 注册 `threading.local` 清理回调。

#### [low] src\omnicrawl\pipeline\_fetch.py:20-26 - browser fetcher 单例跨工作线程共享，线程安全假定
- 现状: `_thread_fetcher("browser")` 在锁内创建一次并共享给所有并发 worker 线程。
- 问题: 同一浏览器实例被多线程并发 `fetch`，其内部并发控制（页面/会话隔离）依赖插件自身线程安全；若插件未加锁，存在竞态。审查范围内无法验证插件实现。
- 建议: 按线程池化 browser 实例并限制并发（参考 `effective_browser_pool`），或在文档中声明 browser fetcher 必须线程安全。

#### [low] src\omnicrawl\pipeline\_builders.py:56-62 - _processor 实例缓存无锁（潜在竞态）
- 现状: `_processor_instances` 为共享 dict，check-then-set 非原子。
- 问题: 当前 `_processor` 仅主线程调用（worker 只跑 `_fetch_checked`），实际未触发竞态；但若未来在 worker 线程调用，两线程会重复构建同一扩展（`build_extension` 可能产生副作用）。
- 建议: 加 `threading.Lock` 保护，或明确 `_processor` 只能主线程调用并注释。

#### [low] src\omnicrawl\pipeline\core.py:95-101 - executor 线程数固定为首次并发值
- 现状: `_get_executor` 仅在 `self._executor is None` 时创建，`max_workers=concurrency`。
- 问题: 同一 Pipeline 第二次 `run(resume=True)` 若配置更高并发，实际 worker 数仍是首次值，`effective_concurrency` 返回的并发与真实值不一致。
- 建议: 需要变化时重建 executor 或按最大值创建。

#### [low] src\omnicrawl\pipeline\_run.py:266-274 - KeyboardInterrupt 期间 drain() 再次触发中断会丢失收尾
- 现状: 取消分支 `drain()` 内调用 `consume`→`_handle_result`；若用户再次 Ctrl+C，异常从 except 块内冒出，`finish_run`/summary 写盘被跳过。
- 问题: run 可能永远停在 running 状态（下次需 `recover_incomplete_runs` 兜底）。
- 建议: 收尾路径再包一层 try/except KeyboardInterrupt，保证 summary 与 finish_run 一定执行。

#### [low] src\omnicrawl\pipeline\_run.py:413 - 循环体内冗余 import time
- 现状: long_poll 分支每轮 `import time`。
- 问题: 重复导入无意义（死代码）。
- 建议: 移到模块顶部。

### pipeline_ops 子包

#### [medium] src\omnicrawl\pipeline_ops\preflight.py:108-118 - run_sample 复用固定 workspace，样本状态跨次累积
- 现状: 样本 workspace 固定为 `preflight_samples/latest`，且不清理。
- 问题: 第二次 `run_sample` 时 `state.sqlite3` 残留上一轮 frontier/records，新样本会带上旧统计、旧 checkpoints；`crawl` 模式 `prepare_cycle(reset_all=False)` 不会清空，结果可能误导。
- 建议: 每次生成带时间戳/递增序号的新子目录，或先用 `recover`/`prepare_cycle(reset_all=True)` 清场。

#### [low] src\omnicrawl\pipeline_ops\preflight.py:46 - 磁盘检查的目标目录在 workspace 不存在时退化到 root
- 现状: `parent = config.workspace.parent if config.workspace.parent.exists() else config.root`。
- 问题: workspace 未创建时检查的是项目根目录所在盘，与真实写入盘可能不同（软链接/挂载场景下误判）。可接受，但值得注释。
- 建议: 显式说明检查对象，或强制 mkdir workspace 后再检查。

#### [low] src\omnicrawl\pipeline_ops\pdf_integration.py:69 - 只扫描顶层 *.pdf，忽略子目录
- 现状: `pdf_files = list(pdf_input.glob("*.pdf"))`。
- 问题: 若 artifacts/pdf 存在子目录（或后续按 run 分组落盘），PDF 会被漏处理，但 manifest（provenance）基于全量 DB 查询，两者口径可能不一致。
- 建议: 使用 `rglob("*.pdf")` 并做去重。

#### [low] src\omnicrawl\pipeline_ops\pdf_integration.py:27-28 - 已存在 project.yaml 时不校验内容
- 现状: `if project_path.is_file(): return project_path, False`。
- 问题: 配置损坏/指向错误输入目录时静默复用，PDF 阶段可能空跑或报晦涩错误。
- 建议: 加载并校验一次 project 配置，失败给出明确提示。

#### [low] src\omnicrawl\pipeline_ops\provenance.py:25-35 - 清单无条件包含所有 run 的 PDF
- 现状: SQL 无 run_id 过滤，`run_id` 仅用于 `current_run` 布尔标记。
- 问题: 新一轮运行会把历史 run 的 PDF 一并带入（`pdf_integration` 里 `documents` 按 pdf_input 文件计数，可能与清单 `documents` 不一致），若属有意为之应在文档写明，否则应按 run 隔离。
- 建议: 明确“全历史”语义，并统一两个统计口径。

#### [low] src\omnicrawl\pipeline_ops\provenance.py:39 - local_path 不做工作区约束
- 现状: `Path(str(row["local_path"])).expanduser().resolve()` 仅 `is_file()` 检查。
- 问题: 相比 `_run.py:323` 的 `workspace in path.parents and not is_symlink()` 检查，这里缺少越界与符号链接防护；DB 被篡改时可指向任意文件（仅读文件名，风险有限）。
- 建议: 与 reprocess 一致，增加工作区约束与 symlink 检查。

#### [low] src\omnicrawl\pipeline_ops\task_spec.py:74 - seeds 非列表时被逐字符迭代
- 现状: `seeds=[str(item) for item in data.get("seeds", [])]`。
- 问题: `seeds` 若为字符串（GUI/API 传错），会拆成单个字符并各生成一条“URL 校验”错误；`max_pages`/`max_depth` 传非数字字符串直接抛裸 ValueError。
- 建议: 入口统一校验类型并给出可读错误。

#### [low] src\omnicrawl\pipeline_ops\plan_compiler.py:38 - seeds 用 str() 强转，非字符串元素产出垃圾 URL
- 现状: `seeds = [str(item) for item in ir.source.get("seeds", [])]`。
- 问题: 元素为 dict/list 时 `str()` 得到 `{...}`，随后被当作种子 URL（TaskSpec.validate 能拦 http(s) 前缀，但原始 TaskIR 路径无此校验）。
- 建议: 校验 seeds 元素类型，非法则报冲突而非静默转换。

#### [low] src\omnicrawl\pipeline_ops\plan_compiler.py:79-84 - plan_hash 基于脱敏配置，凭据变更不影响哈希
- 现状: payload 中 `config`/`permissions` 经 `_redact_for_hash` 脱敏后参与 sha256。
- 问题: 仅密码变化的两个“计划”产生相同 plan_hash，无法用于识别配置漂移；若 plan_hash 承担缓存/审计用途会有误导。
- 建议: 若需审计敏感值变化，可另存“红action 后哈希 + 敏感值指纹”，并在文档说明语义。

#### [low] src\omnicrawl\pipeline_ops\pipeline_stages.py:39 - 未知阶段名的报错来自 tuple.index
- 现状: `positions = [PIPELINE_STAGE_ORDER.index(name) for name in names]`。
- 问题: 阶段名拼写错误时抛 `ValueError: tuple.index(x): x not in tuple`，未指明哪个名字非法。
- 建议: 显式检查并给出含名字的错误信息。

### runtime 子包

#### [high] src\omnicrawl\state\state_store.py:310-323 - claim 的 SELECT→UPDATE 非原子，多进程可双重认领
- 现状: `claim` 在同一事务上下文里先 `SELECT ... WHERE status='pending'` 再 `executemany UPDATE ... in_progress`，WAL 模式下读不阻塞写。
- 问题: 若两个进程（或两个 StateStore 实例）同时 claim，都能读到同一批 pending 行并各自更新 → 同一 URL 被并发抓取两次，重复写 records/响应（`INSERT OR REPLACE` 会掩盖）。当前单实例桌面架构下风险低，但 `LocalWorkerBackend`/recovery 已打开多进程可能性。
- 建议: 用 `BEGIN IMMEDIATE` 包裹 claim，或用 `UPDATE ... RETURNING`（SQLite ≥3.35）原子认领。

#### [medium] src\omnicrawl\state\state_store.py:158-161 - recover_incomplete_runs 无条件重置所有 in_progress
- 现状: 恢复时执行 `UPDATE frontier SET status='pending' WHERE status='in_progress'`（不区分 run）。
- 问题: 若工作区上同时有另一个正在运行的 Pipeline（或多 worker），会把它正在抓的请求打回 pending → 重复抓取。
- 建议: 恢复限定到被恢复 run 的请求，或先做进程存活探测。

#### [medium] src\omnicrawl\state\state_store.py:254-261 - 增量周期 reset_all 把 blocked 也重置为 pending
- 现状: `prepare_cycle(reset_all=True)` 将 `status IN ('done','failed','blocked')` 全部重置为 pending、attempts=0。
- 问题: blocked 是策略性永拒（scope/robots），每周期重入队会反复做无效请求；failed 重置为 pending 还会使“永久失败”的 URL 每周期重试一次。
- 建议: blocked 不重置（或仅用户显式解除）；failed 可重置但保留失败计数语义。

#### [medium] src\omnicrawl\state\state_store.py:728-745 + src\omnicrawl\pipeline\_run.py:228-229 - 每处理一个 URL 就做一次全表聚合统计
- 现状: 主循环对每个完成的 URL 调 `self.state.stats(run_id)`（内部 5 个 COUNT + frontier GROUP BY + quality_stats 查询）。
- 问题: `frontier GROUP BY status` 是全表扫描（走索引也是全量 index scan），百万级 URL 时每页一次 O(n) 聚合，抓取吞吐被显著拖慢。
- 建议: 用增量计数器（内存累加 + 定期落库）替代每 URL 聚合，或只在循环退出后统计一次。

#### [medium] src\omnicrawl\state\schema.py:190-201 - responses 表缺 final_url/url 索引
- 现状: `conditional_headers` 与 `save_response` 都执行 `WHERE (final_url=? OR url=?) ORDER BY id DESC`，但 schema 索引只覆盖 run_id。
- 问题: 每次请求都全表扫描 responses（更新类任务每轮/每次条件请求一条），大表时热点路径退化。
- 建议: 增加 `idx_responses_url ON responses(final_url)`、`idx_responses_url_alias ON responses(url)`（或冗余规范化）。

#### [medium] src\omnicrawl\state\state_store.py:631-651 - review_queue 全表扫描 + 逐行 JSON 解析
- 现状: 为过滤 `evidence._quality.review_required`，先 `SELECT * FROM records{where}` 全量拉取，再逐行 `json.loads`。
- 问题: 记录量大时每次打开审查队列都是全表扫 + 全量解析；可在 SQL 侧用 `evidence_json LIKE '%"review_required": true%'` 预筛，或用一列持久化标记。
- 建议: 加 `review_required` 冗余列并建索引，或至少 LIKE 预筛减少传输。

#### [low] src\omnicrawl\state\state_store.py:341-348 - attempts 跨周期/跨 resume 累积，重试预算失真
- 现状: `claim` 每次认领都 `attempts+1`；`mark_failed` 用 `attempts < max_attempts` 判定。
- 问题: 同一 URL 被多次 resume/换周期重新认领后 attempts 虚高，可能一次真实失败就直接进入 failed（不可重试）。
- 建议: 重试预算改为“连续失败次数”（认领成功后清 0），或在 resume 时重置 attempts。

#### [low] src\omnicrawl\state\state_store.py:227-235 - fail_export 异常会掩盖原始导出异常
- 现状: `_exports.py:51-54` 在 `except Exception` 中调用 `fail_export`；若 fail_export 因 rowcount≠1 抛 ValueError，会替换原始异常。
- 问题: 排错时看到的错误与实际失败原因无关。
- 建议: fail_export 失败时 `LOGGER.exception` 并保持原异常链。

#### [low] src\omnicrawl\state\state_store.py:55-60 - close() 后调用任意方法会 AttributeError
- 现状: `close()` 置 `self.conn = None`，后续方法访问 `self.conn.execute` 直接崩溃。
- 问题: 生命周期错误缺乏防御性报错（应报“已关闭”）。
- 建议: 在方法入口检查 `self.conn is None` 并抛明确 RuntimeError。

#### [low] src\omnicrawl\state\state_store.py:447-449 - _preload_versions 临时表复用不 DROP
- 现状: `CREATE TEMP TABLE IF NOT EXISTS _rv_lookup(...)` + 每次 DELETE/填充。
- 问题: 线程内临时表常驻连接，连接生命周期长时无实际泄漏（受 RLock 保护），但并发新连接会各自创建；属低危，仅记录。
- 建议: 用完 `DROP TABLE` 或改用 VALUES 表值构造，简化逻辑。

#### [low] src\omnicrawl\runtime\resources.py:42,54-62 - workspace 体积检查每间隔全目录 rglob 扫描
- 现状: `_directory_size(self.workspace)` 遍历整个工作区求和。
- 问题: 大工作区（GB 级、几十万文件）下即使有间隔，一次扫描也耗数秒；且每次 check 都重新全量扫。
- 建议: 采样或缓存上次结果 + 增量统计；默认关闭（仅配置上限时启用）。

#### [low] src\omnicrawl\runtime\resources.py:32-33 - check 提前返回的 snapshot 缺 disk/workspace 键
- 现状: 未到间隔时返回 `{"elapsed_seconds": elapsed}`。
- 问题: 调用方 `_run.py:200` 用 `.get("disk_free_bytes") is not None` 防御了，但该 API 形态对其它调用方不直观（键集合不稳定）。
- 建议: 返回一致键集，None 表示未采集。

#### [medium] src\omnicrawl\runtime\execution_backend.py:60-68 - InProcessBackend：service.run() 返回非 dict 时线程死亡、状态卡在 running
- 现状: `run()` 的 `else` 分支在 try/except 之外执行 `result.get("status", ...)`。
- 问题: 若 `service.run()` 返回 None/非 dict，`AttributeError` 在 else 分支抛出 → 线程未捕获而终止，`_state` 永远停留在 `"running"`，界面显示任务在跑但实际已死。
- 建议: 把 `else` 移入 try 或整体包一层，并给 run 结果做类型校验。

#### [medium] src\omnicrawl\runtime\worker_main.py:54-60 - worker 线程 run() 返回非 dict 时状态卡死（同因）
- 现状: `result = self.service.run()` 后 `self.state = dict(result)`，异常只覆盖 `run()` 调用本身。
- 问题: `dict(None)` 抛 TypeError 在线程内未捕获，`state` 停在 `"running"`；客户端 status 永远误报运行中。
- 建议: 对 result 做 `isinstance(result, dict)` 校验，失败置 `"failed"` 并记录。

#### [medium] src\omnicrawl\runtime\execution_backend.py:209-214 - Windows 下 chmod 0600 不生效，会话 token 文件对本地用户可读
- 现状: `worker-session.json` 内含 `auth_token`，`_write_session` 在 `atomic_write` 后 `os.chmod(path, 0o600)`，`except OSError: pass`。
- 问题: Windows 上 `os.chmod` 只切换只读位、不设 ACL；多用户机器上其他本地账户可读该 token，进而通过命名管道向 worker 发送 pause/stop/shutdown 命令（无范围校验）。
- 建议: 用 Windows API/`icacls` 收紧 ACL，或将 token 拆分为 worker 侧启动时通过环境变量/参数传递而不落盘。

#### [low] src\omnicrawl\runtime\execution_backend.py:115-146 - session 文件重复 start 无锁、直接覆盖
- 现状: 两个后端实例（GUI + CLI）同时对同一 workspace `start` 会先后覆盖 `worker-session.json`，先启动的 worker 失去会话记录。
- 问题: 本地多入口场景下会话元数据丢失，attach 可能连到旧地址。
- 建议: start 前检测已存在会话文件并询问/复用。

#### [low] src\omnicrawl\runtime\execution_backend.py:148-156 - 启动握手超时但无任何异常时错误信息为空
- 现状: while 循环内 `return self.status()` 未抛异常时直接成功；超时抛 `RuntimeError(f"...: {last_error}")`。
- 问题: 若 `Client()` 长时间阻塞/无异常返回，`last_error` 为空字符串，报错无信息。
- 建议: 超时时同时报告最近状态与进程是否存活。

#### [low] src\omnicrawl\runtime\worker_main.py:72-74 - shutdown 直接退出进程，运行中任务未优雅收尾
- 现状: shutdown 命令仅 `self._shutdown.set()`，主循环退出并返回 0，进程结束会中断 daemon 任务线程。
- 问题: 抓取中途强杀：SQLite WAL 未 checkpoint、浏览器/抓取器句柄未释放；虽有 `recover_incomplete_runs` 兜底，但不如显式取消。
- 建议: shutdown 前调用 `service.stop()`（若 running）并 `service.close()`，设置合理超时。

#### [medium] src\omnicrawl\runtime\scheduler.py:148-158 - 非法 allowed_hours 使 evaluate_conditions 抛 ValueError，schedule 被租约卡死
- 现状: `run_due` 中 `evaluate_conditions` 不在 try 内；`schedule_conditions.py:13` 对非数字小时 `int(value)` 抛 ValueError。
- 问题: 单个条件值非法 → 整个 `run_due` 中断，且该 schedule 已带 `lease_until=now+3600`、`last_status='running'`，一小时内不再被认领（其他进程同样跳过）。
- 建议: 对 `int(value)`/`float(...)` 包 try 并视为条件不满足 + `defer`；`run_due` 内给 evaluate_conditions 也加异常保护。

#### [medium] src\omnicrawl\runtime\schedule_conditions.py:11 - allowed_hours 用本地时间，next_run_at 用 epoch(UTC)，时区基准不一致
- 现状: `hour = datetime.now().hour`（本地挂钟），调度器 `next_run_at = time.time()`（UTC 纪元）。
- 问题: 机器时区/夏令时变化后，allowed_hours 窗口与 next_run_at 错位，用户在凌晨/切换时区后调度行为不可预期；文档未说明基准。
- 建议: 统一基准（都基于本地或都基于 UTC），并在配置文档明示。

#### [low] src\omnicrawl\runtime\schedule_conditions.py:20-27 - 无电池的机器上 require_ac 被静默忽略
- 现状: `battery = psutil.sensors_battery() if psutil else None`；battery 为 None 时直接跳过全部电池/电源条件。
- 问题: 台式机配置 `require_ac` 后条件恒真，用户得不到任何提示。
- 建议: 返回 `(False, "未检测到电池信息")` 或在 preflight 中提示。

#### [low] src\omnicrawl\runtime\run_control.py:28-37 - 跨进程 read-modify-write 存在丢失更新
- 现状: `update()` 先 `read()` 再 `atomic_write`，锁仅在本进程内。
- 问题: 两个进程同时 pause 时后写覆盖前写（值相同影响小），但“pause 后 stop”等复合操作可能互相覆盖状态位；`atomic_write` 保证不损坏文件但不保证不丢更新。
- 建议: 用文件锁（如 `msvcrt`/`fcntl`）或把 stop/pause 合并为单次写。

#### [low] src\omnicrawl\runtime\recovery.py:148-150 - 时间戳精度不足时隔离目录创建冲突
- 现状: `stamp = utcnow().replace(...)` 后 `quarantine.mkdir(parents=True, exist_ok=False)`。
- 问题: 一秒内两次 `reset_login` 会抛 FileExistsError（未给用户解释）。
- 建议: 附加随机后缀或 `exist_ok=True`（幂等合并）。

#### [low] src\omnicrawl\runtime\recovery.py:131,138 - next_command 中的 config 路径含空格未加引号
- 现状: `f"omnicrawl resume -c {self.config.path}"`。
- 问题: Windows 路径含空格时该命令无法直接复制执行（UX）。
- 建议: 用 `shlex.quote`（Windows 下按需加引号）。

#### [low] src\omnicrawl\runtime\recovery.py:124-131 - continue_incomplete 在无数据库时也创建 run_control.json
- 现状: 先 `RunControl(...).resume()` 再检查 `database.is_file()`。
- 问题: 无状态的任务工作区被“续跑”命令写入了控制文件，略显多余（副作用）。
- 建议: 先检查 DB 存在再写控制文件。

#### [medium] src\omnicrawl\runtime\scheduler.py:143-167 - run_due 串行阻塞执行，长任务拖住后续调度
- 现状: `for schedule in self.claim_due(...): ... executor(config_path)` 同步执行。
- 问题: 单个爬取任务可能运行数小时，期间其它到期任务只能等；若该进程被 cron 按时杀死，所有租约内的任务（含未开始的）都进入“running”失联态。
- 建议: 为每个任务分配独立进程/线程池并设置超时；或在 claim 时按并发上限分批。

### scheduling 子包

#### [medium] src\omnicrawl\scheduling\change_detector.py:406 - 变更历史在内存中无限增长
- 现状: 每次变化 `self._history.setdefault(rule_id, []).append(event)`，event 携带完整前后内容字符串。
- 问题: 长期运行的监控器内存无界增长（每条规则每个版本都保留全文），且 `check_all` 循环中同步顺序抓取所有规则。
- 建议: 历史限制条数（如 deque(maxlen)）或将历史落盘；`check_all` 可并发抓取。

#### [medium] src\omnicrawl\scheduling\change_detector.py:440-446 - save_rules 非原子写入，中断可能损坏规则文件
- 现状: `target.write_text(...)` 直接覆盖。
- 问题: 写一半崩溃 → 规则文件损坏，`load_rules` 的 `except Exception` 静默返回 0，用户规则“消失”且无提示。
- 建议: 用 `atomic_write`（项目 core.utils 已有）。

#### [low] src\omnicrawl\scheduling\change_detector.py:239,251 - 解析器回退使用过宽 except Exception
- 现状: lxml/BeautifulSoup 分支用 `except Exception: pass` 静默回退。
- 问题: 选择器语法错误、内存错误等真实异常被吞掉，最终回退到全文并仅告警“未加载 DOM 解析器”，误导排查。
- 建议: 区分 `ImportError` 与其它异常，其它异常记日志并继续。

#### [low] src\omnicrawl\scheduling\change_detector.py:220-222 - node.text(strip=True) 被调用两次
- 现状: `"\n".join(node.text(strip=True) for node in nodes if node.text())`。
- 问题: 每节点解析两次，纯冗余（微性能）。
- 建议: 用辅助生成器先求值一次。

#### [low] src\omnicrawl\scheduling\change_detector.py:303-309 - 用户正则无复杂度约束（ReDoS 面）
- 现状: `regex:<pattern>` 直接 `re.search(pattern, content, re.DOTALL)`。
- 问题: 配置来源为本地规则文件，但若规则可被导入（后续版本）则存在灾难性回溯风险；pattern 也会被写入日志。
- 建议: 加正则超时（regex 模块）或限制 pattern 长度。

#### [ux] src\omnicrawl\scheduling\change_detector.py:371-375 - 首次检查只建基线，contains/equals/regex 条件在首次绝不触发
- 现状: 首次 `last_hash is None` 时直接记录基线并 return None，`_check_condition` 不执行。
- 问题: 用户创建“contains:关键词”规则后，即使当前页面已含关键词也要等到内容变化才会通知，容易误以为规则失效。
- 建议: 首次检查若条件已满足（如 contains 命中）立即触发事件，并把语义写进文档。

### state 子包

#### [low] src\omnicrawl\state\schema.py:190-201 - 索引覆盖不足（汇总）
- 现状: 已建索引缺少 responses(url/final_url)、frontier(run 相关)、records(request_fingerprint)、artifacts(local_path)。
- 问题: `save_response`/`conditional_headers`、reprocess 的 `records.request_fingerprint` 关联、provenance 的 `lower(local_path) LIKE '%.pdf'` 均为全表扫描。
- 建议: 按上述热路径补索引；`lower(col) LIKE` 无法走索引，改为冗余 `is_pdf` 列。

#### [low] src\omnicrawl\state\schema.py:46-64 - frontier 表无 run_id 归属
- 现状: frontier 无 run_id 列，所有 run 共享同一队列；`enqueue(force=True)` 重置已 done 的 URL。
- 问题: `recover`/`prepare_cycle` 只能全局重置，无法按 run 精细恢复；多 run 并行场景（文档宣称支持分布式）归属混乱。
- 建议: 为 frontier 增加 run_id（或 task_id）维度，并为跨 run 语义制定明确规则。

### 跨文件 / 汇总问题

#### [medium] src\omnicrawl\state\state_store.py:263-275 - retry_failed 无上限默认一次性拉取全部失败行
- 现状: `LIMIT ?` 用 `1_000_000_000` 兜底并 `fetchall`。
- 问题: 大量失败行时一次性加载全部到内存并单条 executemany，内存/事务体积偏大。
- 建议: 分块（如 1000/批）处理。

#### [medium] src\omnicrawl\runtime\redis_frontier.py:44-49 - pop 不校验 seen 且依赖 payload 重建 CrawlRequest
- 现状: `pop` 直接 `zpopmin` + `CrawlRequest(**data)`。
- 问题: ① 与 seen 去重解耦，前面 push 的原子性问题会在此放大；② 若 payload 缺字段（旧版本入队）`CrawlRequest(**data)` 报错，整条队列消费中断。
- 建议: pop 时校验 schema 并用容错反序列化（`**` 前过滤非法键）。

#### [low] src\omnicrawl\runtime\adaptive_execution.py:35,56 - audit 列表无界增长
- 现状: `self.audit.extend(result)` 永不清空。
- 问题: 长期运行内存缓慢增长（与 AutoPilotState.audit 同理）。
- 建议: 限制保留条数或滚动落盘。

#### [low] src\omnicrawl\runtime\auto_pilot.py:293,299-326 - 通过私有属性 `_history._history` 访问
- 现状: `dashboard`/`_last_*` 直接读 `self._history._history`。
- 问题: 耦合内部实现，重构 SignalHistory 即破坏（无类型错误提示）；`dashboard` 的 `history_size` 也借此取值。
- 建议: 在 SignalHistory 上暴露公开只读访问器。

#### [low] src\omnicrawl\runtime\auto_pilot.py:60-76 - analyze 中 n<3 检查冗余（死代码）
- 现状: 开头 `len(self._history) < 3` 返回后，中间又 `if not values` 和 `if n < 3`。
- 问题: 后两个条件永远不成立（values 取最近 10 条，若历史≥3 则 values≥3）。
- 建议: 删除冗余分支。

#### [low] src\omnicrawl\state\state_store.py:257-261,298-299 - enqueue(force) 会把 blocked/done 直接打回 pending
- 现状: `force` 更新 `status='pending', attempts=0`，seed 阶段对每个入口调用。
- 问题: 若上次运行因策略拦截了入口，新 run 重新 seed 会再次尝试（可能属预期）；但“blocked 永拒”语义与 force 重置冲突，需文档明确。
- 建议: 文档化 force 语义，或对 blocked 需显式解除。

#### [ux] src\omnicrawl\pipeline\_run.py:89 - setup 阶段失败摘要不含任何统计信息
- 现状: 失败摘要仅 `{"run_id", "status": "failed", "processed": 0, "error": str(exc)}`。
- 问题: 用户看不到已处理的页面/请求数，排查信息量不足。
- 建议: 附上 `self.state.stats(run_id)`（幂等读取）。

#### [ux] src\omnicrawl\pipeline\_exports.py:43 - 导出冲突错误文本（见前文 f-string 问题）另列 UX 影响
- 现状/问题: 错误信息同时缺失“导出器名”与“如何解决”（需运行 recovery 重置 running 提交），用户无从下手。
- 建议: 报错时提示可用恢复命令。

---

## 附：语法检查

对全部 37 个文件执行 `python -m py_compile`，**全部通过，无任何编译失败**（ALL_OK）。

被检查文件与行数（共 5,211 行）：
- pipeline/__init__.py(9) _builders.py(63) _exports.py(111) _extract.py(204) _fetch.py(117) _mixin_base.py(88) _run.py(486) core.py(109) exporters.py(210) registry.py(42)
- pipeline_ops/__init__.py(1) pdf_integration.py(93) pdf_region.py(43) pipeline_stages.py(41) plan_compiler.py(109) preflight.py(132) provenance.py(73) task_ir.py(242) task_spec.py(211)
- runtime/__init__.py(1) adaptive_execution.py(66) auto_pilot.py(326) execution_backend.py(221) recovery.py(173) redis_frontier.py(74) repository.py(58) resource_profiles.py(39) resources.py(62) run_control.py(69) schedule_conditions.py(32) scheduler.py(167) worker_main.py(90)
- scheduling/__init__.py(7) change_detector.py(464)
- state/__init__.py(17) schema.py(202) state_store.py(759)

## 附：交叉验证结论（排除的疑似问题）

- `CrawlRequest.fingerprint` 仅由 method/url/body/kind 构成，**不包含 headers**（core/models.py:23-37），因此 `enqueue` 时 `redact_headers`（剥离 Authorization/Cookie）不会导致指纹漂移；`_fetch.py:58-63` 构造条件请求重建头也不影响指纹 —— 相关“指纹不一致”担忧排除。
- `run_state.ALLOWED_TRANSITIONS` 覆盖 `paused→running`、`running→cancelled/failed/succeeded` 等全部运行路径，`_run.py` 各取消/失败分支的状态转换合法。
- `exporters.export_all` 的 DuckDB 列名有 `_validate_column_names` 白名单 + 双引号转义（exporters.py:19,169），`claim` 的 ORDER BY 使用白名单字典（state_store.py:303-308），未发现 SQL 注入面。
