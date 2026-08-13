# 成熟项目研究、融合映射与许可边界

最后核验：2026-08-12。

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

## 生态持续观察清单

本节按"采集内核 / 数据清洗 / 文件转换 / AI 驱动"四类，列出值得持续跟踪的生态资源。
维护者每季度核验一次：新出现的优秀实践是否需要吸收？本项目对应模块是否需要升级？
不吸收的能力仍记录在此，便于追溯决策依据。

### 采集内核与浏览器

| 资源 | 关注重点 | 本项目对应模块 | 当前状态 |
|---|---|---|---|
| [awesome-web-scraping](https://github.com/lorien/awesome-web-scraping) | 通用爬虫生态分类法、新框架涌现 | sources/ fetching/ | 持续观察 |
| [awesome-ai-web-scraping](https://github.com/h4ckf0r0day/awesome-ai-web-scraping) | AI 驱动爬虫新范式（LLM 提取、自适应） | extraction/ai_graph.py extraction/intelligent_scraper.py | 持续观察 |
| [Awesome-Web-Scraping（中文）](https://github.com/bright-cn/Awesome-Web-Scraping) | 中文社区实践、本地化场景 | templates/（社交媒体类） | 持续观察 |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | 元素结构指纹 + 选择器自愈 + 置信度分级 | core/structure_fingerprint.py + extraction/adaptive_extractor.py | ✅ 已融合 P2-1（StructureFingerprintRegistry 结构签名生成 + 漂移检测 + 去重）；选择器自愈：持续观察 |
| [Botasaurus](https://github.com/omkarcloud/botasaurus) | 浏览器 Profile 持久化、声明式配置 | fetching/profile_registry.py + fetching/browser_fetcher.py | ✅ 已融合 P2-2（ProfileRegistry 按 canonical 域名+账户+环境独立分配 user_data_dir，LRU 清理） |
| [Colly](https://github.com/gocolly/colly) | 按域名独立并发配额、hook 点设计 | fetching/domain_semaphore.py + fetching/hooks.py + fetching/async_fetcher.py | ✅ 已融合 P2-3（DomainConcurrencyLimiter 双层并发限速 + FetchHooks 三阶段 before_fetch/after_fetch/on_error） |

### 数据清洗与提取

| 资源 | 关注重点 | 本项目对应模块 | 当前状态 |
|---|---|---|---|
| [llm-tab-cleaner](https://github.com/danieleschmidt/llm-tab-cleaner) | 规则失败→LLM 影子修复→人工复核三层 | quality/shadow_repair.py quality/auto_apply.py quality/llm_candidate_generator.py quality/observation_store.py | ✅ 已融合（L0-L3 分级自动化 + **P3-3 L2 观察期/L3 持久化**） |
| [AutoDataCleaner](https://github.com/ELHoussineT/AutoDataCleaner) | 类型推断 + 分级修复（auto-idempotent / auto-llm / review） | `quality/normalizers.py` | ✅ 已融合（L1 幂等 + L2 规则默认开；L3 LLM 槽位默认关，无损性硬约束：推断失败/混合类型不猜） |
| [datatoolkit](https://github.com/AtlasNexusTech/datatoolkit) / [Sieve](https://github.com/Bytosphere/Sieve) | 流式数据管道、算子组合 | services/data_transform.py + commands/transform.py | ✅ 已融合（`omnicrawl transform` 值级变换：--map / --transform-steps，写盘需 --confirm，仅 ALLOWED_FUNCTIONS 白名单算子） |

### 文件转换

| 资源 | 关注重点 | 本项目对应模块 | 当前状态 |
|---|---|---|---|
| [VERT](https://github.com/VERT-sh/VERT) | 格式注册表 + 最短路径图搜索 + 零信任 + 流式分块 | convertx/paths.py + convertx/__main__.py | ✅ 已融合 P3-2（FORMAT_FAMILIES 族目录 + READERS/WRITERS 注册表 + `python -m omnicrawl.convertx --list-paths` 路径枚举） |
| [ConvertX](https://github.com/xieren58/ConvertX) | 异步任务状态机 + 统一进度事件 + 幂等重试 | services/progress.py **&** omnicrawl/convertx/（CLI: `omnicrawl convert`） | ✅ 已融合 P2-4（ProgressTracker 统一进度协议）**+ P3-2（CSV/JSONL/XLSX/Parquet/DuckDB 5×5 互转）** |
| [everythingtohtml](https://github.com/He-wei-gui/everythingtohtml) | 统一文档中间表示 + 下游复用 | document_ir/（计划新建，不重构 pdfx） | 计划借鉴（仅处理 pdfx 不支持的格式） |

### 配置与网址

| 资源 | 关注重点 | 本项目对应模块 | 当前状态 |
|---|---|---|---|
| [Repo Swap](https://github.com/jjdeharo/gitswap) / [Domain Swapper](https://github.com/adamnorwood/domain-swapper) | 域名映射表 + 环境隔离别名 | core/site_aliases.py | ✅ 已融合 P2-5（SiteAliasRegistry normalize + canonical resolve + 循环检测 + 反向 aliases_for） |
| [Dev-Sidecar](https://github.com/docmirror/dev-sidecar) / [FastGithub](https://github.com/dotnetcore/FastGithub) | 多节点健康路由 + 镜像组透明转发 | sources/mirror_registry.py + fetching/async_fetcher | ✅ 已融合 P3-1（EWMA 健康分 + 连续失败摘流 + 每镜像独立走 Egress 二次审计） |

### 不吸收的能力（逆向与反反爬类）

| 资源 | 不吸收原因 |
|---|---|
| Spider King / reverse-skill / Torch / Ghostwire / Argus / Camoufox-reverse-mcp | 与"不绕过验证码、付费墙、站点安全策略"红线直接冲突 |
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | 涉及签名算法逆向、风控对抗，合规风险高；模板库已覆盖同类站点公开数据 |

> 上述项目的**底层方法论**（协议指纹识别、结构签名追踪、决策树路由）在合规场景下有借鉴价值，
> 已在"方法论借鉴边界"小节明确区分：吸收方法、不吸收对抗行为。

## 方法论借鉴边界

本节区分"底层方法论"与"对抗行为"——前者可合规吸收，后者永远不融合。

### 可吸收的方法论

| 方法论 | 合规用途 | 落点 |
|---|---|---|
| 协议指纹识别 | 仅观测公开响应头/HTML/JS 全局变量，识别 API 类型与前端框架 | sources/site_inspector.py |
| 结构签名追踪 | 仅用于 DOM 微调时自动重定位用户已授权的字段 | extraction/adaptive_extractor.py |
| 决策树路由 | LLM 基于确定性检测结果做有限选项决策（非开放式生成） | services/ai_task_designer.py |
| 多路径交叉验证 | 同一字段配 2-3 条候选选择器 + 证据链 | quality/evidence_ledger.py |
| 站点画像 | 合规评估采集难度（CMS + framework + api_type + pagination + data_density） | sources/site_inspector.py |
| API 调用链分析 | 用 Playwright 被动监听正常浏览中的 XHR，自动生成模板草稿 | fetching/action_recorder.py |

### 永远不吸收的对抗行为

| 对抗行为 | 不吸收原因 |
|---|---|
| 验证码破解 | 违反"不绕过验证码"红线 |
| 付费墙绕过 | 违反"不绕过付费墙"红线 |
| 风控规避（签名算法逆向、加密参数生成） | 违反"不绕过站点安全策略"红线 |
| 隐匿身份（指纹伪造用于欺骗） | StealthLevel 仅用于隐私保护，不用于身份欺骗 |
| 未授权访问 | 违反"不进行未授权访问"红线 |
| JS 混淆还原 / Hook 加密函数 | 超出"公开可观测"边界 |

### 分级自动化的安全边界

OmniCrawler 采用 L0-L3 分级自动化（见 `quality/auto_apply.py`），取代"一刀切人工批准"教条。
工程安全网取代人工批准作为主要保障：

| 等级 | 触发条件 | 安全网 |
|---|---|---|
| L0 检测 | 不可逆操作 / 未安全改善 / 所有开关关闭 | 人工批准 |
| L1 幂等自动 | 可逆选择器替换（css/xpath/jsonpath） | 操作幂等，回滚=重跑原值 + 审计日志 |
| L2 高置信自动 | LLM 可用 + confidence≥0.85 + improves_safely + 可逆 | rollback_config_sha256 回滚 + 观察期 + 质量下降自动回滚 |
| L3 持续自动 | L2 连续 3 轮 stable | 大幅下降（<90%基线）才回滚 + 审计日志 |

**硬约束**：
1. 不可逆操作（action 类型）永远 L0
2. 未通过 `improves_safely` 检查永远 L0
3. 自动应用必须写审计日志（`approved_by` 标记 `auto:L1/L2/L3`）
4. 自动应用必须保留 `rollback_config_sha256` 快照
5. L2/L3 必须有质量监控，观察期质量下降自动回滚

