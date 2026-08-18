# 审查报告

审查范围：`src/omnicrawler` 九个子包共 75 个目标文件（services 28 / quality 13 / security 6 / sdk 3 / sources 7 / plugins 8 / review 4 / export 2 / visual_selector 4），全部至少精读一遍，行号以逐块复读为准。审查维度：逻辑、异常、资源泄漏、并发、安全、死代码、性能、跨平台、UX、一致性。项目安全防护整体优秀（EgressBroker 全链路、NetworkTargetPolicy DNS 固定 + fail-closed、secret:// 解析、证据哈希链、插件静态预检、路径包含校验等），本报告只列真实缺陷与改进点，已排除刻意防护设计。报告文件未经授权不修改任何源文件。

## 汇总（按严重级别计数）

| 级别 | 数量 |
| --- | --- |
| critical | 0 |
| high | 1 |
| medium | 6 |
| low | 17 |
| ux | 2 |
| **合计** | **26** |

## 问题清单

### [high] sources\crawl4ai_bridge.py:210-432 - crawl4ai 抓取不经过 EgressBroker，绕过审计/预算/熔断
- 现状：`_require_target`（427-431）每次调用新建 `NetworkTargetPolicy(_C4ANetworkConfig(...))` 做单次 DNS 固定 + fail-closed 校验（SSRF 防护仍在），但 `fetch/fetch_many/deep_crawl/adaptive_fetch` 的实际抓取流量完全不经 egress broker。
- 问题：与项目"所有出网流量进入 egress-audit.jsonl 审计 + 请求/字节/并发预算 + capability 令牌 + 熔断"的承诺不一致。CLI `main()`、`fetch_js_page`、`fetch_stealth` 均为公开入口；`deep_crawl` 默认可抓 100 页而不计入任何预算，安全审计报告会漏掉全部 crawl4ai 出站流量。
- 建议：复用 `egress.request(...)`/`record_response(...)` 包装 crawl4ai 抓取；若定位为显式绕过审计的独立桥接，须在模块文档与 CLI 明确声明并提示用户。

### [medium] services\research_package.py:9 / quality\diagnostics.py:21（及全库） - `from datetime import UTC` 需 Python>=3.11，与 pyproject 声明冲突
- 现状：`requires-python = ">=3.10"`，但 grep 确认以下文件使用 `from datetime import UTC`：`services/research_package.py:9`、`quality/diagnostics.py:21`、`core/utils.py:10`、`core/logging_utils.py:5`、`fetching/retry.py:6`、`pdfx/utils.py:11`、`scheduling/change_detector.py:37`、`gui/views/change_monitor.py:17`、`templates/template_health.py:9`。
- 问题：在 3.10 上 import 直接 ImportError，声明支持的最低版本不可用（跨平台/跨版本缺陷）。
- 建议：统一改用 `from datetime import timezone` + `timezone.utc`，或将 requires-python 明确提升到 >=3.11。

### [medium] services\server.py:35-39 - 本地状态面板 ThreadingHTTPServer 无认证且访问日志静默
- 现状：仅绑定 127.0.0.1:8765，提供 `/api/status` 与 HTML 面板；`log_message` 被重写为静默丢弃。
- 问题：无任何认证/令牌，本机任意进程或同机其他本地用户可读取运行状态；静默日志掩盖访问痕迹，无法审计。
- 建议：保留 127.0.0.1 绑定；增加轻量访问令牌或明确"仅限本机信任用户"文档；至少保留访问日志供取证。

### [medium] services\workspace.py:70-101 `_full_package` - 将整个工作区文件一次性全部读入内存再打包
- 现状：`rglob("*")` 收集所有文件，`entries` dict 持有全部文件 bytes（含原始响应/PDF/附件），随后一次性写 zip。
- 问题：大工作区（数百 MB）打包时内存尖峰，可能 OOM；与项目自身"内存自适应"理念相悖。
- 建议：直接流式写入 zipfile（逐文件 `writestr`/`write`），避免全量缓存。

### [medium] services\quality_report.py:14-28 - run_id 以 f-string 拼入 SQL，存在本地 SQL 注入面
- 现状：`where = (" WHERE run_id=?", (run_id,))` 看似参数化，但 `{where}` 是把 run_id 文本直接嵌入 SQL 字符串（records 与 semantic_changes 两条查询同病）。
- 问题：run_id 若来自 CLI `--run-id` 等用户可控输入，可注入 SQL；虽仅 SELECT，属纵深防御缺陷。
- 建议：改为 `SELECT ... WHERE run_id=?` 直接绑定参数。

### [medium] sources\crawl4ai_bridge.py:93-96,112-125 - llm_api_key 明文存于配置对象且直传第三方 SDK
- 现状：`C4AConfig.llm_api_key` 为明文字段，`_build_extraction_strategy` 原样传入 `LLMExtractionStrategy(api_token=...)`，未做 `resolve_secret_refs`。
- 问题：若用户按项目惯例填 `secret://` 引用，字面量会被直接当令牌发送；与 `core/config.py:201` 全库统一的 secret 解析路径不一致。
- 建议：构造 `C4AConfig`/提取策略时统一调用 `core.credentials.resolve_secret_refs`。

### [medium] visual_selector\server.py:61,211-212 - `_selections` 选择事件列表无上限
- 现状：每次 `msg_type==3` 事件在锁内 `append` 且永不清理（`clear_selections` 需手动调用）。
- 问题：长时间点选会话内存增长无界；GUI 主流程一般只取 `get_selections()` 末尾。
- 建议：改有界 deque（如 500）或超过阈值时提示清空。

### [low] services\workspace.py:159-171 `rollback` - 直接覆盖 state.sqlite3 无运行态/并发检查
- 现状：校验快照位于 snapshots 目录后 `atomic_write` 重写 config 与 state.sqlite3。
- 问题：若流水线正在运行（SQLite WAL 活跃），覆盖数据库可损坏数据。
- 建议：回滚前检查运行锁/提示用户停止任务，或经 sqlite backup/restore 安全替换。

### [low] services\quality_report.py:45 - pii_summary 一次性 json.loads 全部记录到内存
- 现状：`[json.loads(row["data_json"]) for row in rows]` 全量构造列表。
- 问题：大库报告生成内存尖峰。
- 建议：逐行流式统计 PII。

### [low] services\metrics.py:94-152 - average 与 p50/p95 统计口径不一致
- 现状：`average_seconds = totals[key]/count`（全历史累计），而 `p50/p95` 基于有界采样窗口（MAX_TIMING_SAMPLES）。
- 问题：长运行后二者口径分裂，诊断易误导。
- 建议：统一为同窗口均值，或字段注明统计窗口语义。

### [low] services\storage_backends.py:136-150 - S3 put 上传不计入字节预算
- 现状：`egress.record_response(0, ...)` 以 0 字节记录。
- 问题：egress 字节预算只统计下载方向，上传流量漏计。
- 建议：按 `len(payload)` 记账。

### [low] services\regression_library.py:59-84 `load()` - fixture manifest 的 body 路径未做包含校验
- 现状：`self.directory / manifest["body"]` 直接拼接，未校验相对路径/containment。
- 问题：恶意或损坏的 fixture 清单可指向工作区外 gzip 文件并被读取解压（路径穿越读取面）。回归夹具通常本地可信，属纵深防御缺口。
- 建议：校验 body 为相对路径且解析后位于 fixtures 目录内。

### [low] services\workbench.py:92-98 - GUI 运行日志 Text 无行数上限
- 现状：`_write_log` 只插入不清理，长任务日志无限增长。
- 问题：长时间运行内存/渲染开销无界。
- 建议：环形日志（超过 N 行删除头部）。

### [low] services\offline_demo.py:43-47 - report.pdf/scan.pdf 为极简伪 PDF 夹具
- 现状：仅含 `%PDF-1.4` 头与 trailer，无页面对象；scan.pdf 无 trailer 完整结构。
- 问题：被 PDF 处理管线解析成功但内容为空，若被误当真实样例会误导 OCR/提取演示。
- 建议：demo 定位属刻意，建议在 UI/文档标注"占位夹具"，避免下游误用。

### [low] quality\evidence_ledger.py:60,65,90,129 - 各 append 方法无文件写锁
- 现状：`append_node`/`append_lineage`/`append_audit`/`_append_json` 直接以追加模式打开 JSONL 写入。
- 问题：多线程并发追加时行可能交错损坏，破坏证据链完整性（当前多为单写者，风险低）。
- 建议：加全局写锁或原子追加。

### [low] sources\crawl4ai_bridge.py:89,140-141 - proxy 字符串明文凭据直传浏览器配置
- 现状：`C4AConfig.proxy`（`http://user:pass@host:port`）原样进入 `proxy_config={"server": ...}`。
- 问题：代理凭据明文存于配置并直接交给第三方浏览器进程，无脱敏/解析。
- 建议：拆分 userinfo 单独解析传递，避免凭据进入日志与配置镜像。

### [low] sources\crawl4ai_bridge.py:351-354 - deep_crawl 硬编码 join(timeout=600)
- 现状：`thread.join(timeout=600)` 固定 600 秒，超时后 daemon 线程继续运行、结果被丢弃。
- 问题：不可配置且超时后线程仍占资源。
- 建议：超时参数化并纳入线程生命周期管理。

### [low] sources\crawl4ai_bridge.py:406-409 - fetch_stealth/undetected "绕过反爬" 属合规敏感能力
- 现状：`browser_type="undetected"` 用于绕过 Cloudflare/Akamai 反爬。
- 问题：可能违背目标站 ToS/robots，属合规风险面。
- 建议：在 CLI/文档明示"需获授权"，默认不启用。

### [low] sources\sources.py:186-192 - websocket/sse/long_poll/redis/scrapy 全部注册到 GenericSource
- 现状：`register()` 将这些 kind 统一映射 GenericSource，而 GenericSource 仅生成 HTTP 请求、不实现对应传输。
- 问题：用户选择这些来源类型会得到 HTTP 化配置，行为与名称不符，易误导（若无专门实现覆盖）。
- 建议：注册表排除无实现 kind，或补真实实现，避免静默降级。

### [low] sources\site_adapters.py:34-44 - WordPressSource 对 x-wp-totalpages 头 int() 无兜底
- 现状：`int(total_raw)` 与 `int(result.request.meta.get("page", ...))` 无异常处理。
- 问题：非数字响应头/元数据会使 discover 抛 ValueError 冒泡中断爬取。
- 建议：try/except 降级返回空列表。

### [low] plugins\plugins.py:328-351 `_preflight_permissions` - 仅识别 AST 字面量 PLUGIN_METADATA
- 现状：`ast.literal_eval` 失败（metadata 为运行时计算值）时返回空集，`denied` 为空即放行；随后 `_metadata(module,...)` 仍按真实运行时 metadata.permissions 发放 network capability。
- 问题：approved_permissions 门禁可被非字面量 metadata 绕过（load_path 场景，虽属"受信本地插件"）。
- 建议：静态无法读取时 fail-closed 拒绝加载，并强制字面量 metadata。

### [low] plugins\plugin_sandbox.py:43 - 子进程 `-I`（isolated）与 PYTHONPATH 注入冲突，属误导性死配置
- 现状：`-I` 使 PYTHONPATH 完全失效；实际生效的是 plugin_subprocess 的 `sys.path.insert`。
- 问题：`-I` 并未真正隔离（父路径仍被插入），且注释/配置给人已隔离的错觉。
- 建议：删除 `-I` 或真正实现隔离（子进程不注入父路径）。

### [low] visual_selector\server.py:163,176 - `_connected` 为单一布尔量
- 现状：所有连接共享一个布尔状态，任一连接断开即置 False。
- 问题：多浏览器扩展会话时连接状态/页面标题误判。
- 建议：改为连接计数或 per-connection 状态。

### [low] visual_selector\field_converter.py:67 - add_selection 空输入触发 IndexError
- 现状：`selector = ... else self.elements[0].xpath`：元素列表为空且无候选 XPath 时越界。
- 问题：空选择消息可导致字段生成崩溃（异常被上层日志吞掉，无结果）。
- 建议：入参校验，空输入返回空字段。

### [ux] services\benchmark_corpus.py:59-60 - validate_capsule 把合法 cookie 名误报为凭据
- 现状：`any("password" in item or "token" in item for item in cookie_names)`。
- 问题：`csrf_token`、`session_token` 等合法 cookie 名会被误判为"疑似含凭据"。
- 建议：精确匹配凭据样式而非子串，或降级为警告。

### [ux] visual_selector\field_converter.py:130,178 - 未设 seed_url 时默认 "https://example.com" 占位
- 现状：`to_omnicrawler_yaml`/`to_yaml` 在 seed_url 为空时写入 `["https://example.com"]`。
- 问题：生成指向陌生域名的可运行配置，可能被误执行。
- 建议：缺省时在配置中显式标记"待填写"或直接报错提示。

## 说明与已排除项

- 以下前期疑点经复读确认**不成立**，未列入：`services/help_registry.py:8` 的 `user_agent` 导入（实为行 83 使用）；`services/ai_providers.py:55` 的 api_key 明文（secret:// 已在 `core/config.py:201` 全局解析，`provider_from_env` 亦显式 `resolve_secret_refs`）。
- 确认为刻意防护设计（非缺陷）：EgressBroker 全链路审计（egress.py）、NetworkTargetPolicy DNS 固定 + fail-closed（policy.py）、插件静态网络导入预检（plugins.py `_preflight_network_imports`）、`sdk_request` 预算内 SDK 边界标注、诊断 redact 后再写 ZIP、AI 私网端点自动放行（Ollama，user 显式配置）、离线 demo 伪 PDF、`log_message` 静默（配合 127.0.0.1 绑定）。
