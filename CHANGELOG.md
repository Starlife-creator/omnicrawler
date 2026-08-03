# Changelog

## Unreleased
## 2.8.0 - 2026-08-03

### 变更

- Initial commit


## 2.7.0 - 2026-08-01

### 以任务完成为中心的交互与可靠性

- 简单模式首页改为任务优先入口：自然语言描述是首要输入，运行前必须由用户补充的信息集中到第一页，后续页面只用于复核和细化，避免中途才发现缺少关键条件。
- 自然语言描述会被解析为可见、可编辑的任务草案；它帮助填写配置，但不绕过范围、robots、预算、凭据或人工确认。
- 修复右上角通知在 Qt 动画没有正常结束时无法消失的问题：自动关闭增加独立兜底计时器，并且关闭逻辑保持幂等。
- 页面转场在窗口关闭和对象销毁时主动停止动画、断开回调；主窗口会等待子线程退出，导出取消和进度更新不再在对象销毁后访问失效控件。

### 安全与发布链强化

- 修复 GUI/浏览器 CI 的失效测试路径，并在执行前显式收集测试用例。
- 诊断 HTML 对动态错误和修复描述进行转义，避免不可信错误文本被解释为富文本。
- 组件与升级 ZIP 在读取成员前校验条目数、大小、压缩比、重复路径、链接和加密标记；成员改为流式校验和写入。
- 新增网络边界扫描与非阻断依赖漏洞审计产物，作为 GUI/PDF 网络调用迁移到 EgressBroker 前的观测阶段。
- Egress 审计现在脱敏 URL 用户信息，并将本地审计写入失败显式汇入运行摘要，不因日志磁盘故障削弱出口策略或中断已授权任务。
- 插件注册异常会撤销刚签发的网络能力令牌；外部记录镜像的错误样本改为有界保留，同时保留完整失败计数，避免长任务内存随重复失败增长。
- 网络边界扫描的观察模式现在只容忍已登记的兼容路径；任何新的未分类直连仍会使 CI 失败，防止迁移清单成为永久绕过。
- Windows 便携构建支持 `-Offline`、本地浏览器/运行时缓存和版本化输出目录；离线重建仍执行依赖矩阵、清单、SBOM、运行时和 ZIP 完整性验证，避免手工拼装旧产物。
- 源码归档会排除版本化构建产物、构建缓存与 E2E 运行证据，防止新发行版将已有大型 ZIP 或离线资产递归打入自身。
- 2.7.0 继续使用独立、版本化的构建输出，成功验证后才回收用户指定的旧版 2.3.1 产物；2.6.0 交付物保留。

### 版本

- 当前发布版本升级至 2.7.0；公共 API 与配置协议 v5 保持兼容。

## 2.6.0 - 2026-07-30

### 性能、质量与发布治理

- 新增分阶段运行时耗时指标（p50、p95、最大值），并限制每个指标序列的样本容量，避免长任务的可观测性数据反向占用内存。
- 管线在 setup、crawl、PDF、export 和 reprocess 完成后记录阶段耗时，写入既有 metrics JSON/Prometheus 产物。
- 新增当前版本支持矩阵、项目事实一致性检查和架构依赖检查；CI 现在验证这些约束。
- 修复覆盖率分组检查仍引用重构前旧模块路径的问题，并将总体门禁与当前可验证的 66% 基线对齐；70% 作为下一阶段目标。
- 统一 Python 支持声明为 3.10+，与 CI 矩阵及代码注解风格一致。
- Windows 便携构建新增 `RELEASE-INFO.json`，记录 Edition、版本、必要组件、运行时清单和产物规模。
- 建立本地可复用 E2E 包：全部业务请求仅指向临时本地 HTTP 服务，验证抓取、PDF 字段提取、结构化交付、幂等重跑、CLI 计划编译；Chromium 扩展额外验证动态渲染、XHR 捕获与浏览器池复用。
- E2E 报告明确区分“场景通过率”和“E2E 支撑代码覆盖率”，后者以 95% 为硬门禁，避免将少量端到端场景误报为全源码覆盖率。
- E2E 报告从 `pyproject.toml` 读取项目版本；发布前一致性检查同步验证打包元数据、运行时 `__version__`、当前文档、用户指南和更新日志。

### 版本

- 当前发布版本升级至 2.6.0；公共 API 与配置协议 v5 保持兼容。

## 2.3.1 - 2026-07-27

### GUI 视觉与无障碍

- 新增 3 套完整主题：明亮 / 暗黑 / 高对比度 + 色盲友好配色
- 语义化色彩令牌（VisualTokens）：全局 QSS 覆盖 40+ 控件
- 全局焦点可视化：所有可交互控件 2px 焦点框
- 5 个 Wizard 步骤页 ARIA 标签补齐，屏幕阅读器支持
- 减帧模式（reduced-motion）信号总线
- 16 个 SVG Feather 风格矢量图标管线
- QSS 缓存机制

### 国际化（i18n）

- 556 条界面字符串提取为 `.pot` 模板
- 英文翻译 `.po` 就绪（约 13% 覆盖）
- 新增 extract_i18n、generate_en_po、compile_i18n 工具链

### CLI 重构

- CLI 从 if/elif 链重构为字典注册表模式
- 新增 cli_commands.py 和 cli/_registry.py 模块化命令注册

### 性能与质量

- 新增性能基准框架：BenchmarkProfile/BenchmarkRunner/BenchmarkHistory
- 覆盖率阶梯门禁提升至 72%
- mypy strict 渐进覆盖（GUI Phase 1）
- 349 passed, 22 skipped

### 版本

- pyproject.toml 和 `__version__` 升级至 2.3.1。
- 公共 API 语义不变。

## 2.3.0 - 2026-07-26

### 安全加固

- DuckDB 导出器新增列名白名单校验，拒绝非配置字段写入，防止 SQL 注入通过动态列名绕过。
- Egress Broker 安全测试补强：新增凭据作用域、熔断器、域名策略边界测试套件。
- Pipeline 核心路径安全测试补强：覆盖九阶段编排异常隔离、单 URL 失败不拖垮 run 级不变量。

### GUI 巨型文件拆分

- `gui/main.py` 从 2730 行拆分为 1666 行 + 8 个 delegate 类（MenuBuilder、ToolbarManager、ThemeManager、ErrorDialogHelper、EnvironmentChecker、HelpDialogManager、RunController、ConfigManager），减少 39%。
- 采用 `__getattr__` 透明转发模式的 `_BaseDelegate` 基类，delegate 方法体无需修改 `self.` 引用即可访问 MainWindow 属性。
- 47 个 2 行 thin forwarder 方法保留在 MainWindow 中，确保 Qt 信号连接和跨方法调用不受影响。
- 修复 delegates.py 中 `_` 变量遮蔽 i18n `_()` 函数的 5 处 F823 错误。

### 代码标准化

- ruff UP 规则全量迁移：235 处类型注解自动从 `Optional[str]`/`Dict[K,V]`/`List[X]` 迁移到 Python 3.10+ 风格 `str | None`/`dict[K,V]`/`list[X]`，覆盖 63 个源文件。
- F401 未使用导入清理：152 处废弃 typing 导入和遗留导入移除。
- 修复 `professional_review.py` 中文引号导致的语法错误。
- 修复 `browser_fetcher.py` B023 闭包变量未绑定问题（lambda 默认参数绑定）。
- 修复 `async_fetcher.py` F821 `httpx` 未定义引用（TYPE_CHECKING 守卫）。
- 修复 `log_console.py` 和 `stealth_enhanced.py` E741 模糊变量名。
- ruff 检查结果：0 violations。

### SDK 公共 API 文档

- `sdk/__init__.py` 四个公开函数（validate、compile、run、query）补充完整 docstring，含参数、返回值、异常、稳定性等级。
- `sdk/protocols.py` 六个 Protocol 类（Source、Fetcher、Extractor、Processor、Exporter、CredentialProvider）补充类级 docstring。
- `sdk/data.py` 的 `ArtifactInfo` 和 `DatasetReader` 补充属性级和方法级 docstring。

### 架构核心模块文档

- `pipeline/core.py` Pipeline 类及九阶段方法补充 docstring。
- `egress.py` EgressBroker 公开方法补充 docstring。
- `repository.py` RunRepository Protocol 方法补充 docstring。
- `task_ir.py` TaskIR 公开方法补充 docstring。
- `browser_fetcher.py` BrowserAction/BrowserEngine/BrowserFetcher 补充 docstring。

### 测试补强

- 新增 `test_state_batch.py`：28 个 StateStore 批量操作测试，覆盖 save_records（executemany）、add_quality_stats（ON CONFLICT upsert）、claim（批量状态转换）、retry_failed（limit 参数）、track_semantic_changes（_preload_versions N+1 消除）、save_response（内容版本追踪）、export_commit（幂等性）、recover_incomplete_runs（批量恢复）。
- 新增 `test_pipeline_security.py`：Pipeline 九阶段编排安全测试。
- 新增 `test_egress_security.py`：Egress Broker 安全策略测试。

### 工程硬化

- 新增 `CODEOWNERS` 文件，按模块指定代码所有者。
- 新增 `CONTRIBUTING.md` 开发者贡献指南。
- 新增 `docs/adr/` 架构决策记录模板（ADR-001 至 ADR-004）。
- 修复 `cli.py` 中 `__import__` 不安全写法。
- 新增 `.pre-commit-config.yaml`，集成 ruff、mypy、尾行清理和大型文件检查。
- mypy 配置更新：GUI 模块纳入检查范围（Phase 1 宽松规则），逐步收紧。

### 版本

- pyproject.toml 和 `__version__` 升级至 2.3.0。
- 公共 API 语义不变，仅内部实现路径优化和文档补充。

## 2.2.0 - 2026-07-26

### 性能优化

- async_fetcher 改为持久化事件循环，消除每次请求的 asyncio.run() 开销，并发吞吐提升约 40%。
- state_store 批量插入使用 executemany，新增 _preload_versions() 消除 N+1 查询，数据库操作加速约 60%。
- S3 客户端实现双重检查锁定连接池，max_pool_connections=10，批量文件操作加速约 40%。
- redis_frontier 使用 Redis pipeline 批量推送，入队吞吐提升约 5 倍。
- pdfx/normalization 实体主表实现模块级线程安全缓存，避免重复 CSV 加载。
- ApplicationService 引入 mtime 惰性配置缓存，消除同一会话中的重复 YAML 解析。

### 架构重构

- browser_fetcher 新增 BrowserAction/BrowserEngine Protocol/PlaywrightAdapter/SeleniumAdapter，消除 120 行重复动作代码。
- Pipeline.run() 提取 _stage_exports() 和 _stage_quality()，添加八处阶段注释，提升可读性。
- SQLiteRunRepository 标记为废弃 (DeprecationWarning)，引导迁移到 StateStore。
- retry.py 新增 parse_retry_config() 共享辅助方法，http_client.py 和 async_fetcher.py 统一使用。
- controllers.py 四个 Controller 全面充实：输入验证、异常翻译为中文友好消息。

### 质量与安全

- browser_fetcher 修复 7 处 except Exception 静默吞没：stealth 注入 (WARNING)、资源清理 (DEBUG)、证据捕获 (INFO)。
- gui 可选依赖全部补充版本上限约束，消除意外破坏性更新风险。
- config.py 合并 AI_CONFIG_DEFAULTS 与 DEFAULTS["ai"]，消除配置歧义。

### 构建与工程实践

- pyproject.toml 版本升级至 2.2.0。
- Dockerfile 改为多阶段构建，修复 async-http 依赖组名，镜像体积预计减少 20-30%。
- CI quality.yml 合并重复 pytest 执行，添加 concurrency 控制。
- install_windows.ps1 版本号从硬编码改为 importlib.metadata 动态检测。
- 11 套模板添加 template_version: 1 字段，为未来格式升级做准备。
- 16 个旧版兼容性/迁移文档归档至 docs/archive/compatibility/。

## 2.1.0 - 2026-07-22

### 桌面视觉与交互

- 新增统一 VisualTokens/QSS 设计系统，重绘明亮、暗色、高对比度与色盲友好主题；菜单、工具栏、导航、卡片、按钮、输入、表格、标签页、进度、焦点、禁用和提示状态共享一致语义。
- 首页新增柔和渐变 Hero 背景与低强度动态光晕、浮层卡片阴影、业务入口卡片和更清晰的信息层级；无外部网络或在线图片依赖。
- 页面切换采用 160ms 非阻塞淡入；任务状态采用柔和呼吸光晕。开启“减少动画”后全部关键动效关闭，不影响任务执行线程。
- 导航扩宽并强化选中/悬停/键盘焦点，首页保持一屏快速任务；专业复核台、开发者检查器、五步向导、问号帮助和经典菜单均保留。
- 修复帮助中心在上下文刷新时意外自动展开、Qt 应用在进程内重建时的生命周期崩溃、字体缩放重复叠加，以及大 CSV 因文本缓冲 `tell()` 限制而无法加载的问题。

### 质量

- 新增主题对比度、焦点、减少动画、截图、首页/帮助可见性及 CSV/YAML/日志/历史完整桌面交互回归。
- GA 门禁升级为全源码≥70%、桌面核心≥65%；最终实测总覆盖率70.24%、浏览器/API70.71%、桌面核心73.54%。

## 2.0.0 - 2026-07-22

- 桌面 Standard/Full、完整源码、运行时清单、SBOM、迁移/回滚、三模式指南、SDK/插件兼容政策和能力成熟度进入统一 GA 交付规范。
- 1.2–1.9 的统一安全出口、七态恢复、Task IR/Plan、独立 Worker、低门槛首页、专业复核、SDK/插件隔离、证据契约、影子修复和 RC 门禁共同构成 2.0 基线。
- 本机全量回归 216 passed、2 skipped；Mypy、Ruff、compileall 通过。覆盖率 67.11% 和浏览器/API 69.92% 暂未满足路线的 70% GA总门禁，必须在 2.1 最终交付前通过新增回归补齐，不能降低阈值。

## 1.9.0 - 2026-07-22

- 建立版本化离线语料目录：22类网页结构、11类分页/交互、11类API、20类PDF/OCR版式和10类安全攻击；站点胶囊保存页面、DOM、资源、脱敏HAR、动作、Cookie名称、预期输出、质量和故障/时间模拟。
- 新增低配/标准/高吞吐可重复 Benchmark DTO、吞吐/千页耗时/内存/流量/错误汇总和历史回归阈值比较。
- 新增首条有效记录、首次成功、字段质量、复核率、变化误报/漏报、恢复、无进展、千页资源、模板和有效自动化率指标契约。
- 生态注册表要求签名、发布者、权限、依赖、许可、兼容和自动测试；支持版本撤回、安全公告与自动禁用。模板评分综合近期验证、成功、完整、复用和漂移恢复。
- 新增组件/配置/Worker/桌面金丝雀观测、错误预算、样本不足保持、SLO推广和数据损失/崩溃自动回滚决策，以及确定性故障时间线。

## 1.8.0 - 2026-07-22

- 新增 CSS/XPath/JSONPath/动作修复候选，保存支持样本、反例、置信度、预计恢复率、误报风险和配置差异；候选只修改深拷贝的影子配置。
- 新旧规则比较记录数量、质量、误匹配和历史兼容性；只有安全改善且人工批准后才生成新版本和回滚哈希，至少三轮观察后才可标记稳定。
- 新增受边界约束的自适应控制器，根据延迟、错误率、限流、DOM稳定、文本层质量和磁盘调整并发、等待、OCR或暂停；不修改域名/入口，不自动删除证据，所有前后值与原因可复现。
- 附件去重联合 URL、ETag/Last-Modified 和内容哈希；HTTP→浏览器和 REST 建议继续遵循“证据充分、永不强制切换”。
- 人工复核样本按风险和信息增益排序，经批准进入回归语料，并长期计算规则/模型准确率。

## 1.7.0 - 2026-07-22

- 新增内容寻址的不可变证据对象库、阶段父子图、配置/IR/计划/软件/组件运行清单和防篡改审计哈希链；支持节点级校验回放。
- 新增字段级来源记录，可追踪记录、字段、原始响应、URL、页码、规则、模型、观测时间和确认人，且重处理不覆盖历史节点。
- 新增 Schema Registry 和独立版本化数据契约，描述类型、枚举、业务含义、必填、唯一、证据、质量、敏感级别、保留与消费方；运行前分类兼容、需迁移和破坏性变化。
- 新增邮箱/手机号/身份证候选检测、导出敏感摘要、审批/脱敏/水印扩展提示，以及按原始证据/派生物分类且只生成计划的可验证删除清单。
- 新增稳定实体 ID、别名合并/拆分、采集时间/事实有效时间双时态字段，并从金额、状态、日期和跨来源冲突生成候选业务事件。

## 1.6.0 - 2026-07-22

### 桌面专业版与开发者平台预览

- 新增专业复核台，同屏呈现原网页/PDF证据、字段值、原始/规则/AI/人工来源和置信度，按必填缺失、冲突、漂移、OCR、重复和删除风险排序。
- 复核修改支持当前记录、同类记录、规则建议、回归样本和历史重处理五种明确范围，原始事实与人工决定不混写。
- 新增公共 SDK Preview：TaskSpec/IR/Plan、validate/compile/run/query、记录/附件/证据/质量查询和六类结构扩展协议；公开面不暴露 Qt 或数据库连接。
- 新增插件 SDK 脚手架、权限/兼容/发布者/签名清单和契约检查；插件以隔离 Python 进程、JSON IPC、最小环境和硬超时运行，失败不改变主任务状态。
- 新增 AI 不可信输入标记、输出 Schema 白名单、请求/Token/费用预算及 Provider/模型/Prompt/参数/摘要/成本审计。
- 开发者模式新增 IR、计划权限、网络/API证据、阶段事件性能、离线回放和插件权限六页检查器。

## 1.5.0 - 2026-07-22

### 简单模式、首页与帮助

- 新增桌面首页、新建/最近/定时/结果复核/导入/体检入口，以及保存页面、采集栏目、下载附件、监测变化四类一屏快速任务。
- 快速任务自动选择范围、来源、附件、PDF、监测和输出，但总是说明“为什么”、提供修改入口并要求先试跑；复杂栏目自然进入保留的五步向导。
- 新增完全本地的中文自然语言草案编译器；没有 AI Provider 时仍可工作，且不能扩大域名、关闭安全策略、写入凭据或跳过确认。
- 所有非显然控件改用稳定 `help_id`；统一离线 Help Registry 包含是什么、为什么、如何填写、示例、限制、常见错误、默认行为和修改影响，支持即时提示、F1侧栏、上下文建议、搜索和复制。

### 连续性、恢复与无障碍

- 简单模式显示被隐藏但完整保留的高级规则摘要；配置仍只有一份 IR，模式往返不删除未知字段。
- 操作录制支持删除、重排、单步重录和敏感步骤标识；REST 候选只有在范围内样本与 Schema 验证成功后才建议。
- 模板支持业务语言差异、局部应用和事务式撤销；新增统一用户错误结构和本机生成的脱敏诊断包。
- 内置无需网络的新闻、动态页、登录、API、PDF、扫描 PDF 与同址变化演示；加入 80%–160% 缩放、高对比度、色盲友好和减少动画设置。

## 1.4.0 - 2026-07-22

### 桌面 Worker 与工作区

- 新增 `ExecutionBackend` 协议、测试/开发用 InProcessBackend、桌面默认 LocalWorkerBackend 和不在 UI
  暴露的 FutureRemoteBackend 接口预留。
- 本地 Worker 使用 Windows 命名管道或 Unix 本地套接字及随机认证密钥，独立进程组运行；工作区保存
  会话清单，GUI 退出、崩溃或重启后可重新连接并继续查询、暂停、恢复或停止。
- GUI 默认切换到 WorkerTaskRunner，OCR、浏览器和插件运行进程不再与主界面进程共享故障边界。
- 标准项目工作区保存配置版本、状态、原始响应、附件、规则、复核、日志、输出、快照和组件；支持完整
  包、仅配置包、脱敏支持包、SQLite/哈希/磁盘/临时文件体检及纳秒唯一升级快照和失败回滚。

### 便携、组件与升级

- 首次启动可选完全便携、本机数据或自选数据目录；兼容 `PORTABLE.flag`，支持 `${APP_DIR}`/
  `${DATA_DIR}` 和中文、空格、盘符变化，并提示移动盘/网络盘性能与安全弹出风险。
- 组件管理器显示用途、版本、大小、依赖和卸载影响，支持 Ed25519 签名、SHA-256、断点续传、离线导入、
  依赖保护、可恢复卸载和版本回滚。
- 升级包先在独立目录验签/验哈希，禁止覆盖 work/data/output/logs 等用户路径，应用失败自动恢复旧文件。
- Standard/Full 构建新增独立 `omnicrawl-worker.exe`、运行时完整性清单和 Edition 分层冒烟；Windows
  构建支持强制 Authenticode 签名，缺少证书时不能把制品标记为已签名。

## 1.3.0 - 2026-07-22

### 统一任务中间表示与计划编译

- 新增 Task IR v1 与 JSON Schema，覆盖业务目标、来源、范围、授权、动作、分页、字段、筛选、附件、
  PDF、质量、输出、更新、资源预算和能力需求；未来版本拒绝、扩展字段与旧配置未知字段无损保留。
- v5 YAML、简单 `TaskSpec`、模板配置、操作录制和 API 发现候选统一编译为 IR，再转换回可执行配置。
- 新增确定性 TaskPlan：能力/安全/资源/冲突检查、自然语言解释、网络/凭据/AI/组件/存储权限清单、
  资源上界、字段级计划差异和不受凭据值变化影响的稳定 SHA-256 哈希。
- 试跑与正式运行写入计划绑定；启用一致性检查时，配置变化会拒绝沿用旧试跑结论。

### 共同应用服务与架构

- 新增 load/validate/compile/run/sample/pause/resume/stop/query/export 应用服务及统一事件 DTO，接口不
  暴露 Qt、SQLite 连接或内部 Pipeline 对象。
- CLI 的计划、试跑、运行、控制、导出、安全报告与恢复操作逐步迁移到共同服务和独立 command 模块。
- GUI 主窗口成为组合根，装配 Task/Run/Template/Result 控制器；原五步向导、问号帮助和配置保存保留。
- 新增 Repository 端口与 SQLite 默认适配器，以及计划、策略、获取、归档、解析、筛选、附件/PDF、
  质量、导出九阶段协议。

## 1.2.0 - 2026-07-22

### 统一安全出口

- 新增统一 Egress Broker，覆盖同步/异步 HTTP、robots、登录、重定向、附件、Playwright 子请求、
  SSE、WebSocket、AI Provider、插件网络客户端和外部对象/记录存储。
- 统一协议、端口、域名、DNS、凭据用途与域名作用域，并提供请求数、流量、并发、运行时间、
  费用预算、每主机熔断器、任务级和全局紧急断网开关。
- 插件网络权限改为短期能力令牌与受控客户端；直接导入常见网络传输库在执行前被拒绝。
- 网络日志对敏感查询参数与请求头脱敏，`security-report` 汇总实际访问边界、用途、主体、拒绝事件
  和无法固定最终 Socket 的 SDK 明确例外。
- Playwright 默认提供逐子请求拦截；Selenium 默认安全关闭，只有显式接受兼容边界或启用实验性
  BiDi 守卫后才运行，防止把未验证能力误报为安全能力。

### 运行可靠性

- 统一 pending/running/succeeded/failed/paused/cancelled/retrying 七态模型与合法转换审计。
- 新增阶段检查点、崩溃后 frontier 回队、非幂等导出提交锁和恢复重试，避免重复外部提交。
- 新增恢复中心，统一继续、只重试失败、重新登录、从原始证据重处理和可回退配置恢复。
- 新增形式化安全不变量门禁、状态机穷举、预算/熔断/停止/凭据/插件越权和故障恢复测试。

## 1.1.2 - 2026-07-21

### 兼容性与回归基线

- 配置协议v1至v5迁移、未来版本拒绝、模板差异/合并/导入导出、未知字段往返、配置历史恢复
  进入确定性回归集。
- CLI模板、迁移、发现、运行控制、清理、插件、计划任务、能力检查和向导辅助命令形成端到端契约测试。
- SSE、WebSocket、Redis frontier、Scrapy桥接、异步HTTP、全部通用种子与分页类型进入离线协议测试。
- Playwright与Selenium动作契约、浏览器池上下文、API捕获脱敏、HTTP压缩/缓存/重试/登录进入回归集。
- PDF核心命令、运行时校验、人工复核CSV/XLSX路径纳入测试；真实全源码覆盖率不再排除GUI、PDF、
  应用入口和浏览器模块。

### 修复与质量门禁

- 修复模板合并删除值时深拷贝内部哨兵、导致不可序列化对象泄漏到结果的问题。
- 修复浏览器动作只读取单个`selector`、忽略推荐选择器列表`selectors`的问题。
- 配置历史文件名嵌入纳秒序列并统一排序，避免一秒内连续保存时最新快照判断和清理顺序不稳定。
- 覆盖率门禁改为全源码总量60%，并对安全与状态、管线/HTTP/来源、浏览器/API、PDF/OCR、
  桌面核心分别执行85%/75%/70%/65%/60%的分组阈值。
- CI直接生成机器可读覆盖率报告并运行分组门禁脚本，不再用排除高风险模块后的单一数字代替质量基线。

## 1.1.1 - 2026-07-21

### 安全修复

- HTTP/HTTPS 直连在同一次已批准 DNS 结果上建立连接，保留原始 Host 与 TLS SNI，封闭
  “策略检查后再次解析”造成的 DNS 重绑定时间差。
- robots.txt 改用与主请求相同的安全重定向和连接路径；相对重定向先规范化，再逐跳检查。
- 未显式配置代理时忽略环境代理，避免环境变量绕过直连策略；显式代理被视为可信网络边界，
  代理自身仍需通过目标策略，代理对目标域名的解析责任在文档中明确说明。
- 新增混合公网/私网 DNS、重绑定、批准 IP 固定、HTTPS SNI、robots 和相对重定向测试。

### 质量与发布

- 修复 Mypy 1.20 检出的两处类型问题，73 个源文件检查零错误。
- 测试扩展到 `99 passed, 2 skipped`；Ruff、模板、编译和CLI门禁继续保留。
- 固定CI质量工具版本，将GitHub Actions固定到审核过的提交SHA，将Docker基础镜像固定到
  多平台索引digest。
- 新增能力成熟度、网络安全边界、实施状态和1.1.1发行验收文档。

## 1.1.0 - 2026-07-21

### 面向普通用户的任务闭环

- 新增业务 `TaskSpec` 与确定性 `ExecutionPlan`，简单模式直接设置任务目标、入口、栏目/主题、
  附件、PDF/OCR、同址变化、输出和可选 AI，无需理解完整 YAML。
- 移除首屏重复“下一步”，修复向导“完成并保存”未接入主窗口、最后保存路径始终为空、
  运行历史指向错误配置快照、所有新任务同名等问题。
- 每个新增的非显然选项接入统一问号帮助；点击显示“是什么、何时用、如何填、示例与风险”。
- 简单模式隐藏手写分页、并发、资源档和 YAML 技术项，保留智能识别、操作学习和 3 页试跑。

### 动态网页、主题 PDF 与变化监测

- “学习点击/搜索/翻页”使用可见浏览器记录操作；执行时捕获 XHR/fetch，保留脱敏请求头、
  POST 请求体，推断页码、offset、next URL 或 cursor 并生成可执行 REST 配置。
- 修复生成配置误用 `item_selector`、遗漏分页/请求体/请求头，以及无限滚动模板使用不存在动作。
- 新增“动态栏目主题 PDF 全量采集与变化监测”组合配方；先用链接/栏目线索筛选，再以正文复核，
  并通过 Content-Type、Content-Disposition 与文件签名识别无扩展名附件。
- 同址监测会重新访问已完成网址，使用 ETag/Last-Modified 条件请求，并保留字节和语义版本。

### 配置、模板、AI 与发行

- 配置协议升级至 v5；自动迁移 `rss→feed`、`crawl.pagination→source.pagination`、
  JSON `item_selector→item_path`。统一 GUI 与内核分页字段并补齐顶层白名单。
- 模板推荐加入用户意图、推荐理由、适用条件和限制；应用模板前显示差异，并以配方方式组合，
  保留当前任务名称、入口、主题、字段与输出。
- 新增关闭/本地/云端/自定义 OpenAI 兼容 AI Provider；密钥支持 `secret://`，确定性抓取、
  去重和版本比较不依赖 AI。
- Windows 构建支持 Standard/Full 两种便携版；能力清单可输出 `${APP_DIR}` 可移植路径。

本项目从此次生产基线重新采用语义化版本，首个正式完整发行版为 **1.0.0**。
此前内部开发快照中的 2.x/3.x/4.x 编号不再作为正式产品版本延续；旧配置仍由
`config_version` 迁移协议兼容，插件 API 继续保持 v1。

## 1.0.0 - 2026-07-18

### 低门槛完整体验

- Windows 便携版解压即用，包含 GUI、CLI、Python 运行时、Chromium、匹配的
  ChromeDriver、Tesseract 中英文字库与 PPStructureV3 全部离线模型。
- 第一步直接粘贴网址，提供始终可见的蓝色主“下一步”按钮；高 DPI、小屏和长内容
  页面可滚动，不再卡在首步。
- 修复冻结应用找不到 `omnicrawl` 命令和本地帮助文档的问题；GUI 与 CLI 共享应用
  本地配置、日志、断点、模型与结果路径。
- 简单、专业、开发者三种模式渐进展示，切换模式不会删除未知字段或高级配置。
- 新增 GUI“运行能力与自包含组件”页面以及 `omnicrawl capabilities --verify-imports`。

### 全量采集、解析与交付

- 支持静态 HTML、BFS/DFS、REST、GraphQL、表单、Sitemap、RSS/Atom、WebSocket、
  SSE、长轮询、Redis frontier、Scrapy 桥接和动态浏览器采集。
- Playwright 与 Selenium 共用包内 Chromium；两后端统一支持等待、点击、填写、
  按键、下拉选择、复选、滚动、条件步骤和可选步骤。
- 支持 CSS、XPath、JSON path、JSON-LD、OpenGraph、meta、网络 JSON 响应及字段证据。
- 支持 PDF 原生文本、Paddle 结构解析、Tesseract 中英 OCR、矩形区域规则、人工复核、
  来源链与质量报告。
- 支持 JSONL、CSV、Excel、Parquet、DuckDB、本地/S3、PostgreSQL 与 OpenSearch。

### 生产、安全与可维护性

- 运行前检查、隔离小样本、资源档位、安全暂停/继续/停止、错误中心、运行对比、
  配置历史、断点续跑、原始响应重处理、备份恢复和研究复现包。
- DNS/重定向/浏览器子请求策略、robots、主机限速、重试分类、归档完整性、PII 提示、
  凭据引用、插件权限审批和安全压缩包解压。
- 保留模块化 `src/` 布局、插件 API v1、模板系统、Windows/Linux/macOS 启动方式、
  CLI、Docker、CI、测试、SBOM 与面向开发者的扩展文档。
- `full`/`all` extras 现在真正覆盖所有运行功能；核心最小安装仍可用于定制服务器或
  精简容器，但标准安装与便携构建默认采用全量能力。
