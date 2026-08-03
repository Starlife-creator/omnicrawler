# 成熟项目研究、融合映射与许可边界

最后核验：2026-08-04。

本项目吸收公开架构思想、协议行为和工程模式，不复制第三方项目源码。可选依赖通过官方包集成；发布前进入依赖清单与 CycloneDX SBOM。外部站点模板只保存公开协议配置，不复制数据或网页内容。

## 采集内核与浏览器

| 来源 | 核验重点 | OmniCrawler 中的落点 | 许可/策略 |
|---|---|---|---|
| [Scrapy](https://github.com/scrapy/scrapy) | Engine、Scheduler、Downloader、Middleware、Pipeline 分层 | Source/Fetcher/Processor/Exporter 注册表、Scrapy 桥 | BSD-3-Clause；可选适配 |
| [Crawlee Python](https://github.com/apify/crawlee-python) | 请求管理、会话、资源感知并发、路由与生命周期钩子 | 生命周期 hook、资源守卫、浏览器池 | Apache-2.0；设计参考 |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | AI 驱动 JS 渲染、自适应抓取策略、深度爬取、LLM 结构化提取 | 可选 `crawl4ai_bridge.py` 桥接层；`extraction/ai_graph.py` AI 提取 pipeline 借鉴其 HTML 分块→LLM→结构化输出模式 | Apache-2.0；可选依赖 |
| [Playwright Python](https://github.com/microsoft/playwright-python) | BrowserContext 隔离、网络监听和状态管理 | HTTP→浏览器升级、隔离上下文、XHR/fetch 归档 | Apache-2.0；可选依赖 |
| [Heritrix](https://github.com/internetarchive/heritrix3) | 可恢复 frontier、归档、范围和礼貌策略 | SQLite frontier、原始响应、作用域/robots/限速 | Apache-2.0；不复制代码 |
| [Apache Nutch](https://github.com/apache/nutch) | 插件化、分段和抓取数据库 | 插件元数据、可扩展 source/processor | Apache-2.0；不内嵌 Java |
| [StormCrawler](https://github.com/apache/stormcrawler) | 流式拓扑、状态和去重 | WebSocket/SSE/长轮询、Redis frontier | Apache-2.0；不内嵌 |

HTTP 客户端行为核验自 [HTTPX Client/连接池](https://www.python-httpx.org/advanced/clients/)、[资源限制](https://www.python-httpx.org/advanced/resource-limits/)、[超时](https://www.python-httpx.org/advanced/timeouts/) 与 [aiohttp ClientSession](https://docs.aiohttp.org/en/stable/client_reference.html)。实现采用长生命周期客户端、连接上限、分阶段超时、Retry-After 和带抖动的指数退避；标准库仍是零额外依赖回退。

## 解析、文档与质量

| 来源 | 吸收的模式 | 当前实现 |
|---|---|---|
| [selectolax](https://github.com/rushter/selectolax) | 高性能 DOM 解析器作为可插拔层 | Parser 契约；核心保留内置解析和可选 lxml/BeautifulSoup |
| [Trafilatura](https://github.com/adbar/trafilatura) | 正文/元数据与精度回退 | 自动提取与证据输出的扩展方向 |
| [MarkItDown](https://github.com/microsoft/markitdown) | 网页/文档内容 → 结构化 Markdown 转换 | `export/markdown_exporter.py`：抓取结果 → Markdown 导出，支持 card/table/list 风格与 Jinja2 模板 | MIT；设计参考 |
| [Apache Tika](https://github.com/apache/tika) | 统一 MIME 检测与 parser 接口 | Parser 插件、Office/文档扩展点 |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 文档预处理、布局、表格和 OCR 流水线 | 可选 Paddle 后端与结构化结果 |
| [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) | 固定次序预处理和副文本 | PDF 预处理扩展方向；不复制 MPL 源码 |
| [Pandera](https://pandera.readthedocs.io/en/stable/) | schema、Check 与失败案例 | 类型、正则、范围、条件、跨字段、重复与异常规则 |
| [Great Expectations](https://github.com/great-expectations/great_expectations) | Checkpoint=数据+规则+动作、结果留档 | 质量统计、review_required、审计与回写 |

内置 XPath 使用 lxml 可选依赖。selectolax 的 Lexbor 后端许可友好，但未设为强制依赖，以保证基础安装与旧行为稳定。

## 调度、队列、存储与可观测性

| 来源 | 核验重点 | 当前实现 |
|---|---|---|
| [Airflow Scheduler](https://airflow.apache.org/docs/apache-airflow/stable/concepts/scheduler.html) | 调度与执行分离、任务状态 | SQLite 租约式固定间隔调度、CLI/GUI 管理 |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | URL 监控、内容哈希对比、变化通知 | `scheduling/change_detector.py`：MonitorRule + ChangeDetector 引擎，定时检查→哈希对比→diff→桌面通知；GUI 有独立标签页 | Apache-2.0；设计参考 |
| [Prefect](https://github.com/PrefectHQ/prefect) | 状态、重试、超时与缓存 | 可恢复阶段、重试分类、资源硬限额 |
| [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/) | 消费组和待确认消息 | Redis frontier 扩展方向；现有队列和锁适配 |
| [Redis distributed locks](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/) | 所有权 token 与租约释放 | Redis 锁封装、SQLite 本地租约 |
| [PostgreSQL](https://www.postgresql.org/docs/current/datatype-json.html) | JSONB、索引和事务 | 可选 JSONB 记录镜像；SQLite 仍为本地恢复权威 |
| [OpenSearch](https://opensearch.org/docs/latest/api-reference/document-apis/index-document/) | 文档索引与稳定 ID | 可选记录索引镜像、幂等 ID |
| [DuckDB](https://github.com/duckdb/duckdb) | 直接分析 CSV/JSON/Parquet | 可选 Parquet 与 DuckDB 交付 |
| [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python) | API/SDK 分离和可插拔 exporter | 生命周期 hook 可接入；默认无额外遥测依赖 |
| [Prometheus instrumentation](https://prometheus.io/docs/practices/instrumentation/) | 请求、错误、时延与低基数标签 | JSON + Prometheus 指标；主机、阶段和错误类型标签 |

对象存储只依赖通用 S3 协议，并强制保留本地恢复副本；不会绑定特定服务端实现。

## 官方站点/API 行为

- [WordPress REST 分页](https://developer.wordpress.org/rest-api/using-the-rest-api/pagination/)：`page/per_page` 与 `X-WP-TotalPages`。
- [Drupal JSON:API 分页](https://www.drupal.org/docs/core-modules-and-themes/core-modules/jsonapi-module/pagination)：`links.next`。
- [MediaWiki continuation](https://www.mediawiki.org/wiki/API:Continue)：完整传回 `continue` 对象。
- [Discourse API](https://docs.discourse.org/)：公开 JSON 端点和 `more_topics_url`。
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) 与 [访问礼仪](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)：公开元数据、mailto/User-Agent、缓存和退避；摘要可能保留出版方版权。
- [OpenAlex API](https://developers.openalex.org/api-reference/introduction) 与 [认证/计费](https://developers.openalex.org/api-reference/authentication)：API Key、每页 100、当前预算/速率头；数据许可与服务条款分别处理。
- [GitHub REST 分页](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api) 与 [速率限制](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)：Link/页码、匿名额度、Retry-After 和速率头。

这些行为分别落在内置 site adapter、协议模板或保守的公开站点模板中，并记录 `verified_at` 和 `source_urls`。

## 明确不融合的能力

- 验证码破解、付费墙绕过、风控规避、隐匿身份或未授权访问。
- 未核对许可证的代码、模型或模板快照。
- 把第三方强互惠组件源码未经许可合并进核心（本项目已采用 AGPL v3）。
- 宣称一个通用模板能永久适配所有站点；站点变化通过健康记录、快照和插件维护。

## 维护规则

1. 模板必须包含稳定 ID、用途、能力、占位符、核验日期；站点模板还要保存官方资料 URL 和数据/内容许可提示。
2. 第三方依赖先核对官方文档、许可证、维护状态、失败回退，再进入可选 extra。
3. 新插件不得绕过网络范围、robots、响应大小、凭据或审计边界。
4. 每次发布生成 SBOM，并用 CI 验证模板、wheel、GUI、真实浏览器和本地端到端流程。
