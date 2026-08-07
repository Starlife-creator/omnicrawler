# OmniCrawler 完整优化方案（全量覆盖版 v2.0）

> 文档版本：2.0 | 生成日期：2026-08-05
> 依据文档：
> - **源 A（终版）** `FINAL_FINDINGS_SUMMARY.md`：P0#1-24、P1#25-110、P2/P3 主题簇、优化 0-18、根因 1-10、5 条假数据
> - **源 B（问题清单）** `问题清单与优化方案.html`：P0#1-15、P1#16-57、P2#58-125、P3#126-156、方案 1-13、5 根因
> - 覆盖注册表：`docs/OPTIMIZATION_PLAN_TRACKING.md`（两源逐条映射，100%）
> 适用范围：src/omnicrawl 及配套打包/测试/文档
> 目标：按"先止血、后治本、再增强、持续加固"四阶段，以**可验收、可回归**的工作包形式落地**全部**优化项与问题。**覆盖声明：源 A 110/110 条、源 B 156/156 条逐条可追溯。**

---

## 0. 使用说明

- 每个阶段含：目标、任务清单（含位置/操作/验收标准/验证方式）、阶段退出条件。
- 任务编号规则：`S<阶段>.<工作包>.<序号>`，如 `S1.1.1` 为阶段1工作包1.1的第1个任务。
- 覆盖矩阵见 §6；**两源逐条映射见 `docs/OPTIMIZATION_PLAN_TRACKING.md`**。
- 每个任务标注"预期收益"（消除哪些源问题号/簇），便于追踪。
- 阶段内任务可并行，但阶段之间顺序执行；每个阶段结束必须跑 `tests/integration/` 回归。
- `后项` = 并入既有任务验收项（同一任务一并完成）；`S4.5` = P3 批量清理批次。

### 全局纪律（所有阶段强制）

1. 所有"删除/重置/覆盖"操作先入回收站（`core/safe_action.py`），不直接 DELETE。
2. 每阶段开始前备份工作区（`.runtime/`、数据库、配置、docs）。
3. 改动先在测试环境验证，再合入。
4. 编码规范：配置合并一律 `copy.deepcopy`；`or` 仅用于布尔逻辑，默认值用 `if x is None`。
5. 新增代码必须带"消费方存在性"测试（防孤儿代码回归）。
6. CI 中保持 ruff + mypy + 覆盖率门禁不倒退。

---

## 1. 阶段 0：基线建立与防护准备（0.5 天）

**目标：** 建立回归基线、破坏性操作防护和编码规范，为后续阶段提供安全操作环境。

### 任务 S0.1：基线备份与回归快照

| 项 | 内容 |
|---|---|
| 操作 | ① 备份 `.runtime/`、`data/`、`configs/`、`docs/`；② 记录当前 `pytest tests/`、`pytest tests/integration/` 红绿基线；③ 记录 `import omnicrawl` 耗时基线（应约 287ms） |
| 验收标准 | 备份目录存在；基线测试报告存留；耗时基线写入阶段文档 |
| 验证方式 | 查看备份目录；阅读 pytest 输出摘要 |

### 任务 S0.2：破坏性操作统一防护骨架

| 项 | 内容 |
|---|---|
| 操作 | ① 新建 `src/omnicrawl/core/safe_action.py`，提供 `require_explicit_apply(action_name, args)` 装饰器与"先入回收站再删除"工具；② 把 `reset` / `reset_stage` / `rollback-config` 三个命令入口先接入骨架（未实现确认流程则默认 dry-run） |
| 验收标准 | 无 `--apply`/`--yes` 显式参数时，破坏性命令仅输出将执行的动作清单并退出 |
| 验证方式 | 运行 `omnicrawl reset --help`；`omnicrawl reset` 不带确认参数不删除任何数据 |
| 预期收益 | 根因 10 提前止血，防优化过程中误删数据 |

### 任务 S0.3：编码规范落地

| 项 | 内容 |
|---|---|
| 操作 | ① 新增 `docs/CODING_STANDARDS.md`，固化 deepcopy / or 语义 / try 隔离 / 消费方测试规范；② CI 加一条 grep 规则：`if ... or defaults.get(` 或 `dict(` 作为配置合并时告警 |
| 验收标准 | 规范文档存在；CI 规则生效 |
| 验证方式 | 故意引入违规示例，CI 告警 |
| 预期收益 | 根因 1 防止新增代码再次引入 |

### 阶段 0 退出条件
- [x] 基线已记录、备份完成
- [x] `safe_action.py` 可用
- [x] 编码规范 + CI 规则生效

---

## 2. 阶段 1：止血修复（1-2 天）

**目标：** 消除全部 P0（两源 24+15 条并集，崩溃 / 数据损坏 / 安全边界），一次落地方案 1/2/3/4 及 15 的首批迁移。

**任务来源：** 源A P0#1-24 全量 + 源B P0#1-15 全量 + 部分 P1 安全项 + 根因 1/2/3/4/5。

### 工作包 S1.1：崩溃三件套 + GUI 关闭流程（方案 1）

#### 任务 S1.1.1：deep_merge 深拷贝，消除 DEFAULTS 污染

| 项 | 内容 |
|---|---|
| 位置 | `src/omnicrawl/core/utils.py:35-42`；`services/application_service.py:89` |
| 操作 | ① `result = dict(base)` → `result = copy.deepcopy(base)`；② 删除 `application_service.py` 88-89 行就地改写 `config.raw["crawl"]["max_pages"]` |
| 验收标准 | 任务A设 max_pages=50 后，任务B默认值仍为 100；深拷贝单元测试通过 |
| 验证方式 | 新增测试：连续两次 `run()` 验证默认值不被污染；`pytest tests/core/test_utils.py -k deep_merge` |
| 预期收益 | 源A P0#2/#11、源B P0#2 + 根因 1 |

#### 任务 S1.1.2：文件日志 + GUI 启动强制接入

| 项 | 内容 |
|---|---|
| 位置 | `src/omnicrawl/core/logging_utils.py:24-34`；`gui/main.py` 启动入口 |
| 操作 | ① `configure_logging` 增加 `RotatingFileHandler`（`portable_data_root()/logs/omnicrawl.log`，5MB 轮转保留3份）；② stderr 兜底 `sys.__stderr__ or io.StringIO()`；③ GUI 启动时强制调用 `configure_logging`；④ 配置层 warning 改 `logger.warning` 输出，环境变量缺失收集为 warning 列表 |
| 验收标准 | 打包后用 `pythonw.exe` 启动，日志写入文件；无 stderr 时不抛异常 |
| 验证方式 | 手动运行 GUI → 查看 `logs/omnicrawl.log`；单元测试 mock 无 stderr 场景 |
| 预期收益 | 源A P0#3、源B P0#3 + 根因 5 |

**S1.1.2 后项**：非法日志级别不再 AttributeError（源B P2#64）——`setLevel` 前校验级别名，未知级别 fallback INFO + warning。

#### 任务 S1.1.3：step3_fields 统一改用 lxml.html.fromstring

| 项 | 内容 |
|---|---|
| 位置 | `gui/wizard/step3_fields.py:249/269/327/341/345/402/410/423` |
| 操作 | ① 三处 `etree.fromstring(x, etree.HTMLParser())` → `lxml.html.fromstring(str)`；② 启动时检测 lxml 可用性，不可用则禁用智能提取并提示；③ `lxml` 提为必需依赖 |
| 验收标准 | 示例文本选字段、AI 模式、CSS 选择器测试均可运行；中文不乱码 |
| 验证方式 | 打开智能提取对话框逐项点选；`pytest tests/gui/wizard/` |
| 预期收益 | 源A P0#2/#4/#18、源B P0#4 |

#### 任务 S1.1.4：_() 提到模块顶层

| 项 | 内容 |
|---|---|
| 位置 | `gui/main.py:1972-1988` |
| 操作 | `from .i18n import _` 移至模块顶层（移除 headless 条件分支内的 import） |
| 验收标准 | `python -m omnicrawl.gui --run config.yaml` 正常执行；顶层 import 无副作用 |
| 验证方式 | headless 模式冒烟测试 |
| 预期收益 | 源A P0#21、源B P0#5 |

#### 任务 S1.1.5：QThread 销毁与 closeEvent 流程

| 项 | 内容 |
|---|---|
| 位置 | `gui/main.py:1807`；`async_workers.py:395-405`；`pdf_workbench.py:515`；`change_monitor.py:585`；`gui/home.py:352-354` |
| 操作 | ① worker 全部以主窗口为 parent；② `cancel_all` 用单次总预算等待，仍在跑的线程标记并在其 `finished` 时清理；③ closeEvent 走延后关闭流程；④ PDF 工作台补 closeEvent；⑤ change_monitor 加并发守卫 `if self._worker is not None and self._worker.isRunning(): return`；⑥ `_AIEnrichWorker` 完成后 `deleteLater` |
| 验收标准 | 运行任务时关窗口不再出现 "QThread: Destroyed while thread is still running"；PDF 工作台、change_monitor 关闭无崩溃 |
| 验证方式 | 手动：任务运行中关窗 ×10；`pytest tests/gui/` |
| 预期收益 | 源A P0#6/#22/#24、源B P0#6、源A P1#67、源B P1#39（change_monitor 并发）+ 根因 2 |

### 工作包 S1.2：pipeline 异常隔离统一（方案 2）

#### 任务 S1.2.1：drain 提到 finally

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/_run.py:275-283` |
| 操作 | 统一收尾到 `_finalize(run_id, status, summary)`：正常/失败/流式三条路径共用，`drain()` 在 `finally` 中执行 |
| 验收标准 | 任意异常路径下 in_progress 行被 drain/重置；resume 不丢失请求 |
| 验证方式 | 注入异常测试：`pytest tests/pipeline/ -k "drain or finalize"` |
| 预期收益 | 源A P0#10、源B P0#10/P1#36（_run_stream 漏 summary）+ 根因 3 |

#### 任务 S1.2.2：reprocess 记录构造纳入 try

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/_run.py:336-347` |
| 操作 | CrawlRequest/FetchResult 构造与 `read_bytes()` 一并纳入 try，失败计入 failures 继续下一条 |
| 验收标准 | 单条 NULL status_code 或已删除文件不拖垮整个 reprocess 任务 |
| 验证方式 | 构造含坏数据的任务记录 → 运行 reprocess → 其他记录正常处理 |
| 预期收益 | 源A P0#14、源B P0#14 + 根因 3 |

#### 任务 S1.2.3：max_pages=0 语义修复

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/_run.py:95` |
| 操作 | `limit = int(crawl.get("max_pages", 100)) if max_pages is None else max_pages`；`max_pages < 0` 显式报错 |
| 验收标准 | `max_pages=0` 不抓任何页；负值报错信息清晰 |
| 验证方式 | 单测断言两种输入 |
| 预期收益 | 源A P0#8、源B P0#8 |

#### 任务 S1.2.4：独立 attempted 硬上限（方案补充）

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/_run.py:137 vs 152-166` |
| 操作 | 引入独立 `attempted` 计数作为硬上限；或区分 `max_pages`（成功页）与 `max_requests`（总请求，默认 max_pages×5） |
| 验收标准 | 大面积 403 时请求总量不超过 max_requests；max_pages 语义不变 |
| 验证方式 | 模拟 403 场景断言请求次数封顶 |
| 预期收益 | 源A P0#9、源B P0#9 |

#### 任务 S1.2.5：safe_data 工具函数库

| 项 | 内容 |
|---|---|
| 位置 | 新建 `src/omnicrawl/core/safe_data.py` |
| 操作 | 提供 `safe_json_loads`、`safe_int`、`safe_float`、`safe_get`、`safe_slice`；全局替换裸 `json.loads`/`int()`/`float()` 调用点（优先 pipeline、extraction、LLM 响应解析） |
| 验收标准 | 非法输入返回 None/默认值 + warning，不抛裸异常 |
| 验证方式 | 工具函数单测；对替换点运行原有测试 |
| 预期收益 | 源A P0#14 + P1#42/#43、源B P0#14/P2#98/#103 + 根因 3（LLM 空 choices 等） |

### 工作包 S1.3：安全边界闭合（方案 3/6 首批）

#### 任务 S1.3.1：CSV 注入防护补全

| 项 | 内容 |
|---|---|
| 位置 | `src/omnicrawl/core/utils.py:121-125` |
| 操作 | 判断改为 `value.lstrip("\t\r\n ").startswith(("=", "+", "-", "@"))` |
| 验收标准 | `\t=cmd`、`\r@x` 等前缀绕过均被拦截 |
| 验证方式 | 单元测试覆盖 4 种前缀 |
| 预期收益 | 源A P1（CSV 簇）、源B P1#42 |

#### 任务 S1.3.2：归档安全检查空 parts + 穿越/半截文件/大小写/纯"."补全

| 项 | 内容 |
|---|---|
| 位置 | `src/omnicrawl/core/archive_security.py:112`；`fetching/archives.py`；zip 成员处理 |
| 操作 | ① 访问 `parts[0]` 前检查 `if path.parts:`；显式拒绝 `"."`；② 反斜杠穿越 `..\..\evil` 校验（Windows 目录穿越）；③ `copy_zip_member` 先写临时文件再原子 rename，已存在目标先确认；④ ZIP 成员大小写归一化去重仅在 case-insensitive 平台启用；⑤ `_safe_relative` 拒绝纯 `.` |
| 验收标准 | `.` / `./` 成员不再抛 IndexError；反斜杠穿越被拦截；拷贝失败无半截文件 |
| 验证方式 | 构造含 `./`、`..\..\evil` 的归档成员测试 |
| 预期收益 | 源A P0#12 + P1#25、源B P0#12/P2#104/#145/#147 |

#### 任务 S1.3.3：request_payload 脱敏

| 项 | 内容 |
|---|---|
| 位置 | `extraction/api_discovery.py:210-225` |
| 操作 | 写模板前对 request_payload 做脱敏（token/key/password/authorization 字段打码）；复用现有 error_dialog 脱敏风格 |
| 验收标准 | 生成的模板文件中无明文登录 token |
| 验证方式 | 生成模板 → grep 敏感字段 |
| 预期收益 | 源A P1#33、源B P1#54 |

#### 任务 S1.3.4：browser_fetcher 敏感 headers 只对主请求

| 项 | 内容 |
|---|---|
| 位置 | `fetching/browser_fetcher.py:676-683` |
| 操作 | request.headers（含 Auth/Cookie）仅设置到主请求，子资源不广播；对需要认证的子资源提供白名单配置 |
| 验收标准 | 子资源请求不含主请求的敏感 headers |
| 验证方式 | 抓包/代理日志断言子资源请求头 |
| 预期收益 | 源A P1#31、源B P1#55 |

#### 任务 S1.3.5：AsyncClient 固定 IP + 代理策略校验

| 项 | 内容 |
|---|---|
| 位置 | `fetching/async_fetcher.py:48-55,112-114` |
| 操作 | AsyncClient 挂自定义 transport 固定到 `approved_addresses` 返回字面 IP（复用 `http_client.py` 的 `PinnedHTTPConnection`）；代理 URL 先 `policy.require(proxy)`；DNS 重绑定防绕行 |
| 验收标准 | 目标域名 DNS 变化后仍访问已审批 IP；未审批代理被拒绝 |
| 验证方式 | 单测：注入重绑定场景断言访问固定 IP |
| 预期收益 | 源A P0#19、源B（SSRF 簇）+ 根因 4 |

#### 任务 S1.3.6：Playwright 响应尺寸防护

| 项 | 内容 |
|---|---|
| 位置 | `fetching/browser_fetcher.py:713-762` |
| 操作 | 先读 `content-length` 拒绝超大响应；`record_response` 计入预算，超预算即中止（不先整读 body） |
| 验收标准 | 超大响应不导致 worker 内存耗尽；超限响应也计入字节预算 |
| 验证方式 | 模拟超限响应断言拒绝 |
| 预期收益 | 源A P0#20 + P1#30、源B（限额簇） |

#### 任务 S1.3.7：插件权限审批门加固

| 项 | 内容 |
|---|---|
| 位置 | `plugins/plugins.py:226-269` |
| 操作 | 权限审批仅认字面量 metadata 声明；非字面量（动态计算）一律拒绝或要求显式 approve |
| 验收标准 | 动态构造的 metadata 无法绕过审批门 |
| 验证方式 | 构造绕过 payload 单测 |
| 预期收益 | 源A P1（凭据检查簇）、源B P1#32 |

#### 任务 S1.3.8：密钥环境变量前缀修正

| 项 | 内容 |
|---|---|
| 位置 | `core/credentials.py:11` |
| 操作 | `OMNICRAW_SECRET_` → `OMNICRAWL_SECRET_`（补 L）；兼容旧前缀 |
| 验收标准 | 按新前缀读取密钥成功 |
| 验证方式 | 单测设置环境变量后读取 |
| 预期收益 | 源A P1（密钥簇）、源B P1#17 |

### 工作包 S1.4：正则与字段识别修复（方案 4/5 首批）

#### 任务 S1.4.1：价格分类正则 $ 转义

| 项 | 内容 |
|---|---|
| 位置 | `extraction/intelligent_scraper.py:192` |
| 操作 | 正则内 `$` 转义为 `\$`；grep 全项目 `r".*\$.*"` 复核同类错误 |
| 验收标准 | 非价格元素不再被标为"价格"；自动配置字段名恢复多样 |
| 验证方式 | 用含价格/非价格混合页面跑自动配置断言字段名 |
| 预期收益 | 源A P0#1、源B P0#1 |

#### 任务 S1.4.2：TemplateLoader.combine 实现

| 项 | 内容 |
|---|---|
| 位置 | `gui/core/template_loader.py`（被 `gui/async_workers.py:185` 调用） |
| 操作 | 实现 `combine(names)`：seed_urls 合并、fields 合并、其余配置段取第一个模板；冲突时后者覆盖 |
| 验收标准 | 模板合并任务不再 failed；合并结果字段/URL 正确 |
| 验证方式 | 选择2个模板合并 → 断言输出 |
| 预期收益 | 源A P0#5、源B P0#13 |

#### 任务 S1.4.3：SSE EOF 判定

| 项 | 内容 |
|---|---|
| 位置 | `fetching/streams.py:41-57` |
| 操作 | `readline()` 返回空串时增加连续空串计数，超过阈值（如 3）判定连接断开跳出 |
| 验收标准 | 服务端断开后不再忙循环占 CPU；正常事件流不受影响 |
| 验证方式 | 单测：关闭 socket 断言循环退出；CPU 采样无异常 |
| 预期收益 | 源A P0#8、源B P0#11 |

#### 任务 S1.4.4：字段名进 XPath 参数化 + 选择器语义统一

| 项 | 内容 |
|---|---|
| 位置 | `gui/wizard/step3_fields.py:338,345`；高级可视化导入 |
| 操作 | XPath 变量绑定或用 `lxml.etree.XPath` 编译后传参；包裹 `XPathEvalError`；高级可视化点选的 XPath 不再被当 CSS 选择器导入（按选择器类型分流） |
| 验收标准 | 字段名含 `'` 不再崩溃；可视化导入选择器语义正确 |
| 验证方式 | 构造含引号字段名测试；可视化导入冒烟 |
| 预期收益 | 源A P0#17、源B P1#31 |

#### 任务 S1.4.5：analyze_to_config 契约核验 + 占位符校验

| 项 | 内容 |
|---|---|
| 位置 | `extraction/intelligent_scraper.py:466-511` |
| 操作 | 输出按 `core/config.py` 契约逐键核验：pagination 输出 `page`、attribute/attr 统一、is_container/item_selector 对齐、crawl/source 位置一致；输出前过白名单；自然语言描述无 URL 时 `file:///placeholder` 不再通过校验（报错要求补充 URL） |
| 验收标准 | 自动配置产物可通过校验并跑出真实数据；占位 URL 被拦截 |
| 验证方式 | 自动配置 → validate → 试跑 3 个站点断言有数据 |
| 预期收益 | 源A P0#3/#4、源B P1#56 + 根因 6 + "假绿灯" |

### 工作包 S1.5：基础设施类 P0（PDF 事务 + 运行基础）

#### 任务 S1.5.1：pdfx parser 短事务批写（方案 15 首批）

| 项 | 内容 |
|---|---|
| 位置 | `pdfx/parser.py:148-199` |
| 操作 | 改为"先流式解析到内存页列表，再开短事务 executemany"；参照 `pdfx/extraction.py:320-331` 每线程 DB 模式 |
| 验收标准 | 大 PDF 并发解析无 "cannot start a transaction within a transaction"；无 SQLITE_BUSY |
| 验证方式 | 并发解析多个大 PDF 断言无 BUSY 错误 |
| 预期收益 | 源A P0#13、源B P0#7 + 方案15① |

#### 任务 S1.5.2：Pipeline __init__ ExitStack 回滚 + close 异常保护

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/core.py:42-73` |
| 操作 | `__init__` 全程用 `contextlib.ExitStack`，异常时 `stack.close()`；close() 逐项 try/except + 汇总异常 |
| 验收标准 | 任一子系统构造失败时已建资源全部释放（SQLite 连接、fetcher、sink）；fetcher 关闭异常不中断整体 |
| 验证方式 | mock S3/Redis 抛异常 → 断言连接关闭 |
| 预期收益 | 源A P0#15、源B P0#15/P1#43 |

#### 任务 S1.5.3：requires-python 兼容

| 项 | 内容 |
|---|---|
| 位置 | `pyproject.toml:12`；9 个 `datetime.UTC` 文件 + 8 个 `tomllib` 文件 |
| 操作 | 方案A（推荐）：`from datetime import timezone` + `timezone.utc`；`tomllib` 补 fallback（pyproject 兼容 3.10） |
| 验收标准 | Python 3.10 环境可 import 全部模块 |
| 验证方式 | 3.10 venv 跑 `python -c "import omnicrawl"` |
| 预期收益 | 源A P0#23、源B P1#28 |

#### 任务 S1.5.4：commands __all__ 修正

| 项 | 内容 |
|---|---|
| 位置 | `commands/__init__.py:5,11` |
| 操作 | `field_suggest` → `field`，或增加别名导出 |
| 验收标准 | `from omnicrawl.commands import *` 不报 AttributeError |
| 验证方式 | import 冒烟 |
| 预期收益 | 源A P0#14、源B P1#29 |

#### 任务 S1.5.5：Redis frontier 原子性与计数 + fingerprint 去重

| 项 | 内容 |
|---|---|
| 位置 | `runtime/redis_frontier.py:21-42` |
| 操作 | 先 `requests = list(requests)` 再 enumerate；MULTI/EXEC 保证 seen+queue 原子；队列成员用 fingerprint 去重；seen 加 expire |
| 验收标准 | added 计数正确；崩溃后不产生"已 seen 未排队"的丢失项；同 fingerprint 不被重复抓取 |
| 验证方式 | 生成器输入断言计数；注入崩溃场景断言原子 |
| 预期收益 | 源A P0#10 + P1#80/#110、源B P0（Redis 簇）/P2#122 |

#### 任务 S1.5.6：template_monitor None 防护

| 项 | 内容 |
|---|---|
| 位置 | `templates/template_monitor.py:41,50` |
| 操作 | `content_type = result.content_type or ""` 归一后判断；`record.data` 用 `dict(...)` 防御 |
| 验收标准 | content_type=None 不抛 TypeError |
| 验证方式 | 单测 |
| 预期收益 | 源A P0#7、源B P1#38 |

#### 任务 S1.5.7：EasySpider scroll 动作支持 + wait 语义修正

| 项 | 内容 |
|---|---|
| 位置 | `sources/easyspider_bridge.py:271,230-234` → `fetching/browser_fetcher.py:262-270` |
| 操作 | browser_fetcher 增加 "scroll" 动作（等价 scroll_bottom），或 bridge 将 scroll 转为 scroll_bottom；点击 wait 改为点击后延时 |
| 验收标准 | scrollCount>1 的导入任务运行时不再 ValueError；需稳定时间的页面抽取不崩 |
| 验证方式 | 导入含 scroll 动作的任务试跑 |
| 预期收益 | 源A P0#6 + P1#49、源B P1#37 |

#### 任务 S1.5.8：async 抓取器跨 loop 修复

| 项 | 内容 |
|---|---|
| 位置 | `fetching/async_fetcher.py:33-87` |
| 操作 | 不强制 `set_event_loop`；`fetch_many` 检测当前 loop，与客户端绑定 loop 不同则新建客户端；按 loop 缓存+清理 |
| 验收标准 | 插件 loop 调用不报 "Future attached to a different loop" |
| 验证方式 | 两个线程不同 loop 分别调用 fetch_many |
| 预期收益 | 源A P0#9、源B P1#41 |

### 阶段 1 退出条件（回归门禁）
- [x] `tests/integration/` 全绿，无 P0 复现
- [x] 两源 P0 并集（源A P0#1-24 + 源B P0#1-15）覆盖矩阵逐条勾销
- [x] 手动冒烟：CLI run、GUI 智能提取、PDF 并发解析、模板合并
- [x] 运行 `ruff check src/`、`mypy src/` 无新增违规

---

## 3. 阶段 2：功能修复（3-5 天）

**目标：** 配置单一真源、secrets 治理、PDF 容错、0 条语义统一、全部功能错误类 P1/P2 落地，落地方案 5/6/7/8 + 源A P1#25-110 余量。

**任务来源：** 源B P1#16-57 + P2#58-125 全量 + 源A P1#25-110 中未在阶段1覆盖的条目 + "假绿灯/假校验"。

### 工作包 S2.1：配置单一真源 + 校验单一真源（方案 5，根因 6）

#### 任务 S2.1.1：DEFAULTS 单一真源 + 白名单校验

| 项 | 内容 |
|---|---|
| 位置 | `core/config.py`、`validate_config`、`gui` CrawlConfig 序列化 |
| 操作 | ① 提取 `DEFAULTS` 为唯一真源；② `validate_config` 对全部顶层段做白名单检查并支持 strict 模式；③ GUI `CrawlConfig` 序列化只写用户改动键；④ `validate_full_config` 转调 `validate_config`；⑤ `templates render` 与 `auto-analyze` 输出按契约逐键核验；⑥ 补齐 http.engine / processors.pdf.ocr_backend 到 DEFAULTS；导出格式检查补 parquet/duckdb |
| 验收标准 | 未知键默认不拦截但 strict 模式拦截；GUI 配置经 CLI 校验通过；auto-analyze 输出契约一致 |
| 验证方式 | 新增 `test_validate.py` 10 个拼写错误用例；配置往返 e2e（见 S3.3） |
| 预期收益 | 源A P0#4/#16 + P1#83、源B P2#60/#61/#86/#91/#92 + 根因 6 + "假绿灯/假校验" |

**S2.1.1 后项**：`capability mode="quick"` 不再静默丢 require_features（源B P1#30）；`http.engine` 未知值显式报错而非静默回退 urllib（源B P2#75）；URL 补全不再产出 `https://C:\data\page.html` 假校验（"校验绿灯链" F688）。

#### 任务 S2.1.2：配置错误信息增强

| 项 | 内容 |
|---|---|
| 位置 | `core/config.py`（校验输出）、YAML 解析层 |
| 操作 | ① 错误统一中文、带编号、多行（不挤一行）；② YAML 语法错误包装为友好提示（保留原始栈到日志）；③ `${VAR}` 缺失不再静默替换空串，改为 warning+列表汇总 |
| 验收标准 | 坏配置提示可读、可定位；缺失环境变量有汇总 |
| 验证方式 | 构造坏 YAML、缺 VAR 场景断言输出 |
| 预期收益 | 源B P2#58/#59/#62 |

**S2.1.2 后项**：`describe_error` 覆盖 urllib/SSL 异常 + 空 message 兜底（源B P2#65）；登录失败友好提示改为可达（源B P2#70）；KeyError 消息带可用候选（源B P2#78）；benchmark `10%%` 文案（源A P0#16）。

#### 任务 S2.1.3：.env 优先级修正 + 解析健壮化

| 项 | 内容 |
|---|---|
| 位置 | `core/ai_env.py:84-96` |
| 操作 | 读取优先级改为 `reversed()`（项目级覆盖用户级，与文档一致）；.env 解析处理编码异常/行内注释/`export` 前缀 |
| 验收标准 | 项目级 .env 优先于用户级；脏 .env 不抛裸异常 |
| 验证方式 | 两级 .env 设置不同值断言生效者 |
| 预期收益 | 源A P1#81、源B P1#18/P2#67 |

#### 任务 S2.1.4：重试配置双轨合并

| 项 | 内容 |
|---|---|
| 位置 | `fetching/http_client.py:114,263` + `retry.py:8` |
| 操作 | 用户配置的 `retry_on_status` / `retry_max` 生效；默认值统一到 DEFAULTS 单点；`retries` 语义明确（`max(1,...)` 不再把"不重试"改掉） |
| 验收标准 | 配置 retry 后行为随之变化；两处默认值一致；0 可表示不重试 |
| 验证方式 | 单测断言重试行为受配置驱动 |
| 预期收益 | 源A P1#29、源B P1#21/P2#68 + P3#133 |

### 工作包 S2.2：secrets 五出口统一加密 + 脱敏（方案 6）

#### 任务 S2.2.1：secrets_store 基础设施

| 项 | 内容 |
|---|---|
| 位置 | 新建 `src/omnicrawl/core/secrets_store.py` |
| 操作 | AES-GCM + OS keyring 优先 / 用户密码派生 fallback；get/set/delete API；keyring 无可用后端时不抛未捕获异常（自动 fallback 密码派生） |
| 验收标准 | 密钥可存取；OS keyring 不可用时走 fallback |
| 验证方式 | 单元测试 |
| 预期收益 | 源A P1（secrets 簇）、源B P1#27/#32 + 根因 4 补充 |

#### 任务 S2.2.2：六处出口统一接入

| 项 | 内容 |
|---|---|
| 位置 | `config_serializer`（snapshot/autosave/导出）、`settings.ini`（代理池）、`.env`（AI key） |
| 操作 | `from_yaml`/`to_yaml`/autosave/`_full_package`/`settings.ini`/`.env` 六处出口统一经 secrets_store；导出前做"明文凭据扫描"（复用 `preflight.py` 的 `scan_config_file`） |
| 验收标准 | 快照/autosave/导出/ini/env 无明文凭据落盘 |
| 验证方式 | 导出配置包 → grep 敏感键 |
| 预期收益 | 源A P1#76、源B P1#32 + P2#93 + secrets 簇 |

#### 任务 S2.2.3：cookie 原子写 + 加密落盘

| 项 | 内容 |
|---|---|
| 位置 | cookie 存储模块（CookieSession 持久化） |
| 操作 | cookie 落盘改为"临时文件 + 原子 rename"；加载失败显式告警而非静默 pass；内容经 secrets_store 加密（chmod 0600 在 Windows 无效，需真加密兜底） |
| 验收标准 | 中途崩溃不残留损坏 cookie 文件；cookie 文件无明文 |
| 验证方式 | 注入写入中断断言原子性；读盘断言无明文 |
| 预期收益 | 源B P2#71/#72 |

#### 任务 S2.2.4：plan_compiler 脱敏补全

| 项 | 内容 |
|---|---|
| 位置 | `pipeline_ops/plan_compiler.py:102-109` |
| 操作 | `_redact_for_hash` 补 Authorization/Bearer 头与中文密钥键名；`plan -o` 导出前做明文凭据扫描，命中即拒绝或脱敏 |
| 验收标准 | plan 导出与哈希无凭据泄漏 |
| 验证方式 | 含 Authorization 配置导出 → grep |
| 预期收益 | 源A P1#47 |

### 工作包 S2.3：PDF 容错与多进程降级（方案 7）

#### 任务 S2.3.1：OCR 多进程预检与降级

| 项 | 内容 |
|---|---|
| 位置 | `pdfx/ocr.py:244-253,355-359` |
| 操作 | 进入多进程前在本进程调用一次 `create_backend` 预检；`ProcessPoolExecutor` 外层 try/except（BrokenProcessPool, Exception）按 D13 语义标记 skipped 并写 errors |
| 验收标准 | 缺依赖/GPU 不可用时提示"依赖缺失"，不崩管线 |
| 验证方式 | mock 依赖缺失场景断言降级 |
| 预期收益 | 源A P1#84、源B P1#53 |

#### 任务 S2.3.2：LLM 客户端构造容错

| 项 | 内容 |
|---|---|
| 位置 | `pdfx/extraction.py:317` |
| 操作 | `create_llm_client` 包 try：失败记 warning、client=None、纯规则模式继续 |
| 验收标准 | API Key 为空时规则抽取仍执行 |
| 验证方式 | 空 Key 配置跑抽取断言不中断 |
| 预期收益 | 源A P1#85、源B P1#52 + 根因 3 |

#### 任务 S2.3.3：Tesseract 语言归一

| 项 | 内容 |
|---|---|
| 位置 | `pdfx/ocr.py:128` |
| 操作 | 非标准 lang 归一（ch→chi_sim）；GUI OCR 检测按 `config.ocr.backend` 分支判断 |
| 验收标准 | `backend: tesseract` + `lang: ch` 正常 OCR |
| 验证方式 | 跑 OCR 断言成功 |
| 预期收益 | 源A P1#86、源B P1#48 |

#### 任务 S2.3.4：service 阶段隔离补全 + GUI 失败识别 + failed 短路

| 项 | 内容 |
|---|---|
| 位置 | `pdfx/service.py:94-116`；`gui/views/pdf_workbench.py:629-667`；`pdfx/cli.py` |
| 操作 | ocr/export_text 补 try/except 与 ingest/parse 一致；`_on_done` 检查所有 stage 的 failed/stopped 标志，有失败走 `_on_failed` 或明确标注部分失败；cli 阶段链统一走 `service.run_processing`；前序阶段 failed 时短路后续阶段（资源超限不再硬跑 PDF） |
| 验收标准 | 部分阶段失败不显示"✓ 全部完成"；failed 后不执行剩余阶段 |
| 验证方式 | 构造 OCR 失败 → GUI 显示部分失败；资源超限场景断言短路 |
| 预期收益 | 源A P1#89、源B P1#24/#47/P2#100 |

#### 任务 S2.3.5：PDF 计数口径与导出结构（附带）

| 项 | 内容 |
|---|---|
| 位置 | `pipeline_ops/pdf_integration.py:69`；`pdf_workbench.py:656-660` |
| 操作 | PDF 统计用 rglob 递归；GUI 导出文件遍历改 `files.values()`；自定义 `local_directory` 不再被硬编码路径绕过（对象存储路径改配置驱动） |
| 验收标准 | 子目录 PDF 计数一致；导出显示真实路径；自定义目录生效 |
| 验证方式 | 冒烟 |
| 预期收益 | 源B P2（pdfx medium 簇）+ 源A P1#72 |

#### 任务 S2.3.6（= S2.3.7）：pdfx 类型白名单补全

| 项 | 内容 |
|---|---|
| 位置 | `pdfx/config.py:106` + `normalization.py:211-239` |
| 操作 | 类型白名单补 `boolean/entity/relationship`；EntityResolver/boolean 分支接可达；`entity_master_csv` 链路恢复 |
| 验收标准 | `type: boolean/entity/relationship` 可被 from_dict 接受并走对应分支 |
| 验证方式 | 构造该类字段配置跑抽取 |
| 预期收益 | 源A P1#88 |

### 工作包 S2.4：0 条语义与退出码统一（方案 8）

#### 任务 S2.4.1：0 条也算失败 + 三态语义

| 项 | 内容 |
|---|---|
| 位置 | `run`、`run_task._print_summary`、`worker_task_runner`、`headless_runner`、`core/run_state.py:7-13` |
| 操作 | ① `run` 退出码 `0 if (status=="succeeded" and effective_records>0) else 1`；② 0 条打印引导提示（目标无数据/出网被拦截/模板未匹配 → doctor）；③ JSON 摘要恒输出 effective_records；④ 透传 rc；⑤ 保留独立状态 `partial_success`（completed_with_errors 不再映射 succeeded）；⑥ `--strict` 控制 0 条即非 0（默认关闭向前兼容） |
| 验收标准 | 0 条任务 rc=1；partial_success 可被 GUI/CLI 识别 |
| 验证方式 | 造 0 条任务断言退出码；状态机单测 |
| 预期收益 | 源A P1（0条簇）、源B P1#23/P2#88 + "假绿灯" 0 条分支 + CI/cron 可感知失败 |

### 工作包 S2.5：其余功能错误类 P1/P2 全量（含新增）

#### 任务 S2.5.1：金额单位匹配修正

| 项 | 内容 |
|---|---|
| 位置 | `pdfx/normalization.py:69-70` |
| 操作 | 补充"百万""百元"等大单位置于"万"前；加"千万元"回归测试；外币检测补 `$100`/`USD 100`/`€50` 符号形式 |
| 验收标准 | "3千万元" 命中"千万"→ 3×10^7；外币不再静默按人民币标准化 |
| 验证方式 | 回归测试 |
| 预期收益 | 源A P1#28、源B P1#20/P2#102 |

#### 任务 S2.5.2：reprocess 幂等导出刷新

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/_run.py:363` |
| 操作 | reprocess 后强制刷新输出文件（绕过幂等提交跳过逻辑） |
| 验收标准 | reprocess 后导出文件反映最新记录 |
| 验证方式 | 修改记录 → reprocess → 检查导出 |
| 预期收益 | 源A P1#78、源B P1#45 |

#### 任务 S2.5.3：state_store claim 原子化

| 项 | 内容 |
|---|---|
| 位置 | `state/state_store.py:310-323` |
| 操作 | claim() 用条件 UPDATE（`WHERE status='pending'`）返回影响行数替代 SELECT→UPDATE |
| 验收标准 | 多进程无双重认领 |
| 验证方式 | 并发 claim 测试 |
| 预期收益 | 源A P1#26、源B P1#51 |

#### 任务 S2.5.4：流式模式参数透传

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/_run.py:50-51` |
| 操作 | 流式模式透传 run() 全部参数；取消/进度回调生效 |
| 验收标准 | 流式模式取消与进度回调可用 |
| 验证方式 | 流式试跑 + 取消 |
| 预期收益 | 源B P1#26 |

#### 任务 S2.5.5：crawl4ai 走 EgressBroker + 指纹含 headers

| 项 | 内容 |
|---|---|
| 位置 | `sources/crawl4ai_bridge.py:210-432` |
| 操作 | crawl4ai 抓取接入 EgressBroker（审计/预算/熔断）；移除直连路径；metadata None 防护；status_code 真实透传；请求指纹含 headers（多语言/多身份采集不再误去重） |
| 验收标准 | crawl4ai 请求全部出现在审计日志且受预算约束；指纹区分不同 headers |
| 验证方式 | 抓取后查审计；超预算断言熔断 |
| 预期收益 | 源A P1#48、源B P1#33/#46 + 根因 4 |

#### 任务 S2.5.6：压缩解码补全

| 项 | 内容 |
|---|---|
| 位置 | fetching 层解码逻辑 |
| 操作 | 支持 br/zstd 解码（或显式声明不支持并告警） |
| 验收标准 | br/zstd 响应不再以压缩字节当正文 |
| 验证方式 | 构造 br 响应断言正文解码 |
| 预期收益 | 源B P2#69 |

#### 任务 S2.5.7：sources seed 保留全部请求

| 项 | 内容 |
|---|---|
| 位置 | `sources/sources.py:29-47` |
| 操作 | 配置分页时 `seed()` 不再丢弃 `requests[1:]`，全部 seed 请求进入分页逻辑 |
| 验收标准 | 多 seed 配置全部分页抓取，无静默丢失 |
| 验证方式 | 多 seed 配置试跑断言 URL 覆盖 |
| 预期收益 | 源A P1#32 |

#### 任务 S2.5.8：CookieSession 线程安全

| 项 | 内容 |
|---|---|
| 位置 | `fetching/session.py:38-46` |
| 操作 | 进程级单例的 jar 读写统一锁（save 与 HTTPCookieProcessor 共用同一把锁）；或改为线程局部 session |
| 验收标准 | 并发读写 cookie jar 无竞态 |
| 验证方式 | 多线程读写压力测试 |
| 预期收益 | 源A P1#44 |

#### 任务 S2.5.9：async Retry-After 封顶

| 项 | 内容 |
|---|---|
| 位置 | `fetching/async_fetcher.py:155-163` |
| 操作 | `Retry-After` 封顶（如 60s）+ 告警；超限不再静默睡 2 小时 |
| 验收标准 | 服务端返回大 Retry-After 时等待受控 |
| 验证方式 | mock 返回 7200 断言等待上限 |
| 预期收益 | 源A P1#73 |

#### 任务 S2.5.10：routing SPA/挑战页检测修正

| 项 | 内容 |
|---|---|
| 位置 | `fetching/routing.py:24` |
| 操作 | SPA 根节点正则放宽（匹配含 root 子元素的容器，不再要求空根）；挑战页特征词收窄防误判 |
| 验收标准 | 渲染型页面升级浏览器抓取；普通页不误判挑战 |
| 验证方式 | 构造两类页面断言路由决策 |
| 预期收益 | 源A P1#97、源B P2#73 |

#### 任务 S2.5.11：browser fetch 超时取消机制

| 项 | 内容 |
|---|---|
| 位置 | `fetching/browser_fetcher.py:524-526` |
| 操作 | fetch 超时后关闭后台页面/context，不再让任务继续渲染堆积；重试前确认资源已释放 |
| 验收标准 | 超时后浏览器进程/上下文不泄漏，池不退化 |
| 验证方式 | 构造慢页面断言超时后 context 关闭 |
| 预期收益 | 源A P1#98 |

#### 任务 S2.5.12：Selenium 默认可用 + BiDi 异常放行

| 项 | 内容 |
|---|---|
| 位置 | `fetching/browser_fetcher.py:443-450,454-466` |
| 操作 | 默认配置下 Selenium 引擎不再直接 raise（调整 guard 默认值或显式提示需开启的开关）；BiDi guard 对非 PermissionError 异常放行请求而非挂死 |
| 验收标准 | 默认配置 Selenium 可用或给出清晰指引；BiDi 异常不挂死 |
| 验证方式 | 默认配置跑 Selenium 引擎；注入异常断言放行 |
| 预期收益 | 源A P1#96/#99 |

#### 任务 S2.5.13：browser 配置代理 context 键修复

| 项 | 内容 |
|---|---|
| 位置 | `fetching/browser_fetcher.py:642-646,659-661` |
| 操作 | context 键含配置代理（`meta.get("proxy") or config.http.proxy`）时，`_new_context` 同时读取；按代理区分 context 复用 |
| 验收标准 | 配置代理对 Playwright 生效；会话隔离不被破坏 |
| 验证方式 | 带/不带代理请求断言 context 分配 |
| 预期收益 | 源A P1#95 |

#### 任务 S2.5.14：extractors 正则/JSON 容错 + field_designer 性能

| 项 | 内容 |
|---|---|
| 位置 | `extraction/extractors.py:163,227-231,337`；`extraction/field_designer.py:55,199` |
| 操作 | 用户正则经 `safe_regex` 编译；`match.group(group)` 越界防护；`json.loads` 用 `safe_json_loads` 并带 URL/上下文；field_designer 增加节点上限并避免 O(n²) 全树重复遍历 |
| 验收标准 | 病态正则不卡死；非 JSON 响应不崩流水线；大页面不冻结数分钟 |
| 验证方式 | 构造病态正则/非 JSON 响应断言 |
| 预期收益 | 源A P1#42/#100/#101、源B P2#116 |

#### 任务 S2.5.15：ai_graph 空 choices 防护

| 项 | 内容 |
|---|---|
| 位置 | `extraction/ai_graph.py:310` |
| 操作 | `data.get("choices",[{}])[0]` 改判空列表；空结果记 warning 降级 |
| 验收标准 | LLM 返回 `{"choices":[]}` 不抛 IndexError |
| 验证方式 | mock 空 choices 断言降级 |
| 预期收益 | 源A P1#43 |

#### 任务 S2.5.16：record_sinks fail_open 改 fail_closed

| 项 | 内容 |
|---|---|
| 位置 | `services/record_sinks.py:251` |
| 操作 | sink 崩坏时默认使运行失败（fail_closed），不再静默丢弃记录；提供显式 fail_open 配置 |
| 验收标准 | sink 故障被感知并反映到运行状态 |
| 验证方式 | mock sink 抛错断言运行失败 |
| 预期收益 | 源A P1#41 |

#### 任务 S2.5.17：workspace 流式打包

| 项 | 内容 |
|---|---|
| 位置 | `services/workspace.py:70-101` |
| 操作 | `_full_package` 改流式写出（zip 逐文件），排除 SQLite 与旧导出；不整读多 GB 内存 |
| 验收标准 | 多 GB 工作区打包内存可控 |
| 验证方式 | 构造大工作区断言内存峰值 |
| 预期收益 | 源A P1#39 |

#### 任务 S2.5.18：offline_demo 合法 PDF

| 项 | 内容 |
|---|---|
| 位置 | `services/offline_demo.py:43-47` |
| 操作 | 演示 PDF 生成合法页树/xref（用 PyMuPDF/ReportLab 生成），PDF/OCR 演示路径真实可走通 |
| 验收标准 | 演示 PDF 可被 PyMuPDF 打开并 OCR |
| 验证方式 | 跑演示断言解析成功 |
| 预期收益 | 源A P1#40 |

#### 任务 S2.5.19：scheduler finish KeyError + lease 缩短

| 项 | 内容 |
|---|---|
| 位置 | `runtime/scheduler.py:96-134` |
| 操作 | `finish()` 对已删调度兜底（KeyError 捕获，不再中断整批循环）；`claim_due` 租约缩短（3600s → 可配置，默认大幅缩小） |
| 验收标准 | 调度被删后循环不中止；进程死后租约快速回收 |
| 验证方式 | 删除调度触发 finish；模拟进程死亡断言租约回收 |
| 预期收益 | 源A P1#35 |

#### 任务 S2.5.20：recovery mkdir 同秒冲突

| 项 | 内容 |
|---|---|
| 位置 | `runtime/recovery.py:148-150` |
| 操作 | `quarantine.mkdir` 时间戳加随机后缀或改 `exist_ok=True` |
| 验收标准 | 同秒两次 reset 不再 FileExistsError |
| 验证方式 | 连续两次 reset 断言成功 |
| 预期收益 | 源A P1#36 |

#### 任务 S2.5.21：execution_backend 会话权限

| 项 | 内容 |
|---|---|
| 位置 | `runtime/execution_backend.py:145-156,209-214` |
| 操作 | Windows 上 session 文件不再依赖 chmod 0600（补 IPC 握手 token 或放入用户私有目录）；启动超时错误信息非空 |
| 验收标准 | 本地其他用户无法连接 IPC；超时错误可读 |
| 验证方式 | 检查 session 目录权限；注入超时断言信息 |
| 预期收益 | 源A P1#37 |

#### 任务 S2.5.22：doctor 探测走 EgressBroker

| 项 | 内容 |
|---|---|
| 位置 | `services/doctor.py:15-32` |
| 操作 | `_probe_models` 改经 EgressBroker/安全 opener 探测；不再 `urllib.request.urlopen` 直连 |
| 验收标准 | doctor 探测受策略约束，不探测私有目标 |
| 验证方式 | 配置私有 base_url 断言被拒 |
| 预期收益 | 源A P1#38 |

#### 任务 S2.5.23：import-easyspider --ir 生效

| 项 | 内容 |
|---|---|
| 位置 | `cli/_main.py:215` + `_handlers.py:173` |
| 操作 | 处理函数读取 `--ir` 参数并执行导入规则改写；不再静默 no-op |
| 验收标准 | `--ir` 传递后行为变化可观察 |
| 验证方式 | 命令行冒烟断言输出 |
| 预期收益 | 源A P1#45 |

#### 任务 S2.5.24（并入 S3.4.1 后项）：exporters xlsx 静默 pass

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/exporters.py:112-117` |
| 操作 | xlsx 缺 openpyxl 时显式告警（与 parquet/duckdb 一致），不再静默丢弃 |
| 验证方式 | 缺依赖跑 xlsx 导出断言告警 |
| 预期收益 | 源A P1#46 |

#### 任务 S2.5.25：redis fingerprint 去重 + seen expire（并入 S1.5.5）

| 项 | 内容 |
|---|---|
| 位置 | `runtime/redis_frontier.py:21,35` |
| 操作 | zset 成员用 fingerprint 而非完整 payload；`seen` 集加 expire |
| 验证方式 | 构造同 fingerprint 不同 meta 请求断言不重复抓取 |
| 预期收益 | 源A P1#80/#110 |

#### 任务 S2.5.26：AutoPilot 双向调整

| 项 | 内容 |
|---|---|
| 位置 | `runtime/auto_pilot.py:253-274` |
| 操作 | `maybe_adjust` 支持升/降双向（当前只降不升）；磁盘保护提案不再被应用循环静默丢弃 |
| 验收标准 | 负载下降后可回升并发；磁盘保护真正生效 |
| 验证方式 | 构造低负载场景断言回升 |
| 预期收益 | 源A P1#79、源B P2#109 |

#### 任务 S2.5.27：resources rglob 缓存 + audit 裁剪

| 项 | 内容 |
|---|---|
| 位置 | `runtime/resources.py:54-62` + `adaptive_execution.py:56` |
| 操作 | 工作区 rglob 加 mtime 缓存；audit 日志有界（按条数/时间裁剪） |
| 验收标准 | 高频查询不再全扫；audit 不无限增长 |
| 验证方式 | 两次查询断言第二次走缓存 |
| 预期收益 | 源A P1#75 |

#### 任务 S2.5.28：markdown_exporter dict evidence

| 项 | 内容 |
|---|---|
| 位置 | `extraction/markdown_exporter.py`（`_render_card`） |
| 操作 | `f['evidence'][:200]` 对 dict 证据改安全截断（`str(...)[:200]` 或键子集），不再 KeyError |
| 验收标准 | 带 dict evidence 的记录用 card 样式导出不崩 |
| 验证方式 | 构造 dict evidence 导出断言 |
| 预期收益 | 源A P1#77 |

#### 任务 S2.5.29：stealth_enhanced 一致性

| 项 | 内容 |
|---|---|
| 位置 | `fetching/stealth_enhanced.py:366,515,232,397` |
| 操作 | 时区覆盖表补全（America/Chicago 等）；sec_ch_ua 版本号联动；默认探测 IP 可关且不泄漏代理公网身份；`new_page()` 不再建空白标签页 |
| 验收标准 | 指纹字段互不矛盾；无不可关闭的外部探测 |
| 验证方式 | 生成 100 组指纹断言一致性 |
| 预期收益 | 源A P1#74 |

#### 任务 S2.5.30：quality_report 容错 + SQL 参数化

| 项 | 内容 |
|---|---|
| 位置 | `quality/quality_report.py:19,45` |
| 操作 | `json.loads(evidence_json)` 用 safe 解析；NULL/畸形单元格跳过并计数；run_id 不再 f-string 拼 SQL（参数化） |
| 验收标准 | 脏数据不中断报告；无 SQL 注入面 |
| 验证方式 | 构造 NULL/畸形行断言报告生成；run_id 特殊字符断言安全 |
| 预期收益 | 源A P1#34 |

#### 任务 S2.5.31：主题匹配递归 + filter 深拷贝

| 项 | 内容 |
|---|---|
| 位置 | 主题匹配 / `evaluate_topic` 模块 |
| 操作 | 列表/嵌套 dict 字段参与主题匹配（递归遍历，tags:["财报"] 可命中）；多字段不再空格拼接（跨字段假命中）；`filter_records` 深拷贝再写，不污染调用方 record dict |
| 验收标准 | 嵌套字段关键词命中；跨字段不误命中；导出不被 topic 污染 |
| 验证方式 | 构造嵌套字段用例断言匹配；filter 后原 dict 不变 |
| 预期收益 | 源B P2#114/#115 + P3#151 |

#### 任务 S2.5.32：TableProcessor 尊重 extract.fields

| 项 | 内容 |
|---|---|
| 位置 | `extraction` TableProcessor |
| 操作 | `extract.fields` 配置参与表列选择/过滤，不再被忽略 |
| 验收标准 | 配置 fields 后输出列符合预期 |
| 验证方式 | 配置 fields 跑抽取断言列 |
| 预期收益 | 源B P2#117 |

#### 任务 S2.5.33：提取异常阶段归类

| 项 | 内容 |
|---|---|
| 位置 | pipeline 提取阶段错误记录 |
| 操作 | 提取阶段异常记为 `stage="extract"`（不再一律 `fetch`）；日志写"提取失败"而非"抓取失败" |
| 验收标准 | 排障方向正确（阶段/日志语义一致） |
| 验证方式 | 构造提取异常断言 stage 字段 |
| 预期收益 | 源B P2#118 |

#### 任务 S2.5.34：discover_links 去重/协议过滤

| 项 | 内容 |
|---|---|
| 位置 | `sources` discover_links |
| 操作 | 链接去重；过滤伪协议（`javascript:`/`mailto:`/`tel:`/`void(0)`） |
| 验收标准 | `javascript:void(0)` 不再进待抓 URL |
| 验证方式 | 构造含伪协议页面断言过滤 |
| 预期收益 | 源B P2#119 |

#### 任务 S2.5.35：export 空库检查

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/exporters.py` export() |
| 操作 | 数据库不存在时显式报错（提示先跑采集），不再静默创建空库返回空结果 |
| 验收标准 | 空库导出报清晰错误 |
| 验证方式 | 指向不存在 DB 导出断言报错 |
| 预期收益 | 源B P2#120 |

#### 任务 S2.5.36：run_finished 事件兜底

| 项 | 内容 |
|---|---|
| 位置 | pipeline 事件发布 |
| 操作 | 异常路径也发 run_finished（finally 中），监听方不再永久等待 |
| 验收标准 | 任何退出路径事件必发 |
| 验证方式 | 注入异常断言事件收到 |
| 预期收益 | 源B P2#121 |

#### 任务 S2.5.37：增量统计替代全表聚合

| 项 | 内容 |
|---|---|
| 位置 | pipeline 统计逻辑 |
| 操作 | 每 URL 处理只做增量计数/汇总，不再全表聚合 |
| 验收标准 | 高频小页面下 DB 不被拖垮 |
| 验证方式 | 压测断言无全表扫描 |
| 预期收益 | 源B P2#123 |

#### 任务 S2.5.38：retry_failed 分页

| 项 | 内容 |
|---|---|
| 位置 | retry_failed 命令 |
| 操作 | 按批次拉取（默认限额 + 分页），不再一次性全量读入 |
| 验收标准 | 大规模失败场景内存可控 |
| 验证方式 | 构造万级失败行断言分批 |
| 预期收益 | 源B P2#124 |

#### 任务 S2.5.39：sdk.run 默认值对齐

| 项 | 内容 |
|---|---|
| 位置 | SDK `run()` |
| 操作 | `require_sample_match` 默认值对齐 CLI/GUI 行为 |
| 验收标准 | SDK/CLI/GUI 三种入口语义一致 |
| 验证方式 | 三入口对比断言 |
| 预期收益 | 源B P2#125 |

#### 任务 S2.5.40：fingerprint/content_hash 缓存

| 项 | 内容 |
|---|---|
| 位置 | fingerprint / content_hash 计算处 |
| 操作 | 按资源标识缓存哈希（mtime/size 变化失效），避免每次访问重算大响应体 |
| 验收标准 | 重复访问不再反复哈希 |
| 验证方式 | 两次访问断言第二次走缓存 |
| 预期收益 | 源B P2#66 |

#### 任务 S2.5.41：插件加载缓存 + options 隔离 + 实例锁

| 项 | 内容 |
|---|---|
| 位置 | plugins 注册与 processor 管理 |
| 操作 | `build_registry` 结果按加载上下文缓存（不再每个 Pipeline 重跑）；`_processor` 按插件名分配独立 options；`_processor_instances` 加锁 + 实例不复用跨线程 |
| 验收标准 | 多 Pipeline 不重复加载；同类插件独立配置；无 check-then-act 竞态 |
| 验证方式 | 两次建 Pipeline 断言加载一次；并发访问断言无竞态 |
| 预期收益 | 源B P2#76/#79/#80 |

#### 任务 S2.5.42：StateStore 关闭防护 + claim 原子（并入）

| 项 | 内容 |
|---|---|
| 位置 | `state/state_store.py:310-323,752-759` |
| 操作 | close() 后方法调用返回受控错误而非 AttributeError；`INSERT OR REPLACE + FK` 补 ON DELETE CASCADE；`enqueue(force=True)` 不再重置 in_progress/done 的 attempts；`rows()` 不接受任意 SQL（白名单列）；claim 用条件 UPDATE（并入 S2.5.3） |
| 验收标准 | close 后安全失败；重放 REPLACE 不删依赖行；rows() 无注入面 |
| 验证方式 | close 后调用断言受控；并发 claim 断言无双重认领 |
| 预期收益 | 源A P1#26、源B P1#44/P3#156 |

#### 任务 S2.5.43：wait(inflight) 超时

| 项 | 内容 |
|---|---|
| 位置 | pipeline wait 逻辑 |
| 操作 | `wait(inflight, return_when=FIRST_COMPLETED)` 增加 timeout 参数，超时返回可取消状态 |
| 验收标准 | 任务挂起时 wait 可超时返回 |
| 验证方式 | 注入永不完成的 in-flight 断言超时 |
| 预期收益 | 源B P1#35 |

#### 任务 S2.5.44：调度租约/并行/时区统一

| 项 | 内容 |
|---|---|
| 位置 | `runtime/scheduler.py` + `ScheduleStore.claim_due` |
| 操作 | 租约过期可回收（避免并发执行第二次）；`run_due` 并行执行（长任务不拖住后续调度）；`allowed_hours` 与 `next_run_at` 统一 UTC 基准 |
| 验收标准 | 无重复领取；长任务不阻塞调度；时区判定一致 |
| 验证方式 | 并发/长任务/跨时区用例断言 |
| 预期收益 | 源B P2#108/#110/#111 |

#### 任务 S2.5.45：线程局部 fetcher 关闭

| 项 | 内容 |
|---|---|
| 位置 | fetching 线程局部 fetcher 生命周期 |
| 操作 | 线程局部 fetcher 随线程结束关闭（threading.local 清理钩子），消除 HTTP/httpx 连接池泄漏 |
| 验收标准 | 反复创建线程无连接泄漏 |
| 验证方式 | 线程压力测试断言连接数 |
| 预期收益 | 源B P1#49 |

#### 任务 S2.5.46：long_poll 增量落库

| 项 | 内容 |
|---|---|
| 位置 | `fetching/streams.py` long_poll |
| 操作 | 逐条/分批落库，不再等收集完全部消息；中途失败保留已收部分 |
| 验收标准 | 中断不丢已收数据 |
| 验证方式 | 中断 long_poll 断言已收消息落库 |
| 预期收益 | 源B P1#57 |

#### 任务 S2.5.47：InProcessBackend 状态修复

| 项 | 内容 |
|---|---|
| 位置 | `runtime/execution_backend.py` InProcessBackend |
| 操作 | `service.run()` 返回非 dict 时正常结束并置终态；异常路径状态不卡 running |
| 验收标准 | 非 dict 返回不线程死亡；状态恒有终态 |
| 验证方式 | 返回非 dict 断言状态 |
| 预期收益 | 源B P2#113 |

#### 任务 S2.5.48：限速器统一

| 项 | 内容 |
|---|---|
| 位置 | fetching 限速逻辑 |
| 操作 | 单个与批量请求合并到同一限速器实例（按主机），消除实际速率翻倍 |
| 验收标准 | 单/批并发下同一主机速率一致 |
| 验证方式 | 并发请求断言速率 |
| 预期收益 | 源B P2#74 |

### 阶段 2 退出条件（回归门禁）
- [x] `tests/integration/` 全绿
- [x] 配置往返 e2e：GUI 配置 → CLI 跑产 N 条
- [x] 无明文凭据落盘（grep 审计）
- [x] 0 条任务退出码符合预期
- [x] 源B P1#16-57 + P2#58-125 覆盖矩阵逐条勾销
- [x] ruff / mypy 无新增违规

---

## 4. 阶段 3：体验优化（1-2 周）

**目标：** GUI 线程模型重构、孤儿代码接线、契约测试补齐、导出器修正、GUI 级 P1 全量，落地方案 9/10/11。

**任务来源：** 源A P1 GUI 交互类 + 源B P2 系统性簇 + 根因 2/7。

### 工作包 S3.1：GUI 线程模型重构（方案 9，根因 2）

#### 任务 S3.1.1：BackgroundWorker 基类 + 6 处阻塞点改造

| 项 | 内容 |
|---|---|
| 位置 | 新建 `gui/core/background_worker.py`；改造 env_checker、pip 安装、Markdown 导出、PDF 扫描、PyMuPDF 渲染、run_preflight |
| 操作 | ① 建立基类（QThread + 信号回传 + 取消/清理）；② `env_checker.py:60-69` subprocess 移入线程；③ `delegates/env_checker.py:166` try_auto_install 移入线程；④ 其余 4 处阻塞点同模式接入（含 `run_preflight` `main.py:1136-1147`） |
| 验收标准 | 点击"运行"不再冻结；pip 安装期间界面可交互 |
| 验证方式 | 手动：运行检查时拖动窗口、切换标签；`pytest tests/gui/` |
| 预期收益 | 源A P1#50/#51/#61、源B P1#22 + 根因 2 |

#### 任务 S3.1.2：导航/向导/状态机常量化

| 项 | 内容 |
|---|---|
| 位置 | `gui/main.py:885-895`；`gui/design_system.py:603`；`gui/main.py:1811-1837,915` |
| 操作 | ① nav 行号转 `class NavIndex` 常量（修复"结果与复核"错页）；② `_rebuild_wizard()` 操作 wizard_splitter 成员而非外层 wizard_layout；③ 托盘 `QMenu(self)` 接管所有权 |
| 验收标准 | 首页快捷入口跳转正确；向导重建无错对象；托盘右键不崩 |
| 验证方式 | 点击各快捷入口断言落地页；重建向导断言布局 |
| 预期收益 | 源A P0#15 + P1#60/#90/#91、源B P1#19 + P2（wizard 错对象） |

#### 任务 S3.1.3：运行历史记录修正

| 项 | 内容 |
|---|---|
| 位置 | `gui/delegates/run_controller.py:121` |
| 操作 | 启动时存 `mw._running_task_id`，结束时用它而非当前配置 task_id |
| 验收标准 | 运行中切换配置，历史记录归属正确 |
| 验证方式 | 运行中切换配置断言历史 |
| 预期收益 | 源A P1#52、源B P1#40 |

#### 任务 S3.1.4：Toast 死区与内存治理

| 项 | 内容 |
|---|---|
| 位置 | `gui/widgets/toast.py:284`；`gui/widgets/log_console.py:176,196-209` |
| 操作 | ① ToastOverlay 缩小鼠标事件死区（仅 toast 区域拦截）；类型标注返回一致；② `_all_logs` 增加裁剪上限（如 5000 行）+ 过滤重渲染增量式 |
| 验收标准 | 右侧 360px 不再全高死区；长时间运行内存平稳；过滤不卡顿 |
| 验证方式 | 手动点击 toast 覆盖区域；运行 1h 采样内存 |
| 预期收益 | 源A P1#63/#68、源B P1#（toast 簇）/P2#97 |

#### 任务 S3.1.5：无托盘静默停止防护

| 项 | 内容 |
|---|---|
| 位置 | `gui/main.py:1786-1809` |
| 操作 | 无托盘图标且任务运行时，关闭窗口给出确认对话框（停止并退出 / 最小化到后台 / 取消） |
| 验收标准 | 运行中任务不再被静默 stop() |
| 验证方式 | 无托盘运行任务 → 关窗 → 断言弹确认 |
| 预期收益 | 源A P1#94、源B P1（GUI 簇） |

#### 任务 S3.1.6：PDF 工作台取消态修复

| 项 | 内容 |
|---|---|
| 位置 | `gui/views/pdf_workbench.py:692-700` |
| 操作 | worker 取消路径统一发 `all_done`（带 stopped 标志）；`_cancel` 等待线程结束并清理注入环境变量 |
| 验收标准 | 取消后 UI 恢复可操作；`PDFX_LLM_API_KEY` 无残留 |
| 验证方式 | 取消长任务断言 UI 状态与 env |
| 预期收益 | 源A P1#56、源B P1#34 |

#### 任务 S3.1.7：gui/main 顶层副作用移除

| 项 | 内容 |
|---|---|
| 位置 | `gui/main.py` 模块顶层 |
| 操作 | 模块导入不再改环境变量/读 sys.argv/打印帮助；迁入 `main()` 显式调用 |
| 验收标准 | `import gui.main` 无副作用 |
| 验证方式 | import 后断言环境/argv 未变 |
| 预期收益 | 源B P2#87 |

#### 任务 S3.1.8：WorkerTaskRunner.start 残留清理

| 项 | 内容 |
|---|---|
| 位置 | GUI WorkerTaskRunner.start |
| 操作 | 先 `backend.start` 成功再 save_yaml；启动失败清理残留配置并报错 |
| 验收标准 | 启动失败不残留配置文件 |
| 验证方式 | mock backend 失败断言无残留 |
| 预期收益 | 源B P2#89 |

#### 任务 S3.1.9：result_table 导出对话框接线

| 项 | 内容 |
|---|---|
| 位置 | `gui/views/result_table.py:736-745` |
| 操作 | Excel 导出 QProgressDialog 的"取消"真正中断导出；Markdown 导出移入后台线程（不再主线程全量读 CSV） |
| 验收标准 | 取消立即生效；大表 Markdown 导出不冻结 |
| 验证方式 | 导出中断断言行数停止；手动导出断言界面可交互 |
| 预期收益 | 源A P1#62 |

#### 任务 S3.1.10：pdf_region_selector 异步渲染

| 项 | 内容 |
|---|---|
| 位置 | `gui/views/pdf_region_selector.py:118,133` |
| 操作 | PyMuPDF 页面加载/渲染移入 worker，分页懒加载 |
| 验收标准 | 大 PDF 页面选择不冻结 |
| 验证方式 | 打开多页 PDF 断言界面响应 |
| 预期收益 | 源A P1#57 |

#### 任务 S3.1.11：env_checker 取消回退提示

| 项 | 内容 |
|---|---|
| 位置 | `gui/delegates/env_checker.py:36` |
| 操作 | 取消文件夹对话框后不静默回退 portable；保留上次有效选择并提示 |
| 验收标准 | 用户明确选择不被静默丢弃 |
| 验证方式 | 取消对话框断言提示与选择保留 |
| 预期收益 | 源A P1#58 |

#### 任务 S3.1.12：help_center 未知 id 防护

| 项 | 内容 |
|---|---|
| 位置 | `gui/help_center.py:98-101` |
| 操作 | 未知 help_id 不写 `_current_id`，显示兜底帮助而非 KeyError |
| 验收标准 | 复制示例不再崩溃 |
| 验证方式 | 构造未知 id 断言兜底 |
| 预期收益 | 源A P1#59 |

#### 任务 S3.1.13：help_dialog tmp 清理

| 项 | 内容 |
|---|---|
| 位置 | `gui/delegates/help_dialog.py:32-35` |
| 操作 | 选择器指南临时文件用 context 管理或对话关闭时删除 |
| 验收标准 | 每次帮助视图不泄漏 tmp 文件 |
| 验证方式 | 反复打开帮助断言 tmp 目录 |
| 预期收益 | 源A P1#65 |

#### 任务 S3.1.14：error_dialog 正则误伤

| 项 | 内容 |
|---|---|
| 位置 | `gui/delegates/error_dialog.py:46` |
| 操作 | 脱敏正则只匹配凭据上下文（key= 等），不再替换任意问号 |
| 验收标准 | 普通句子中的问号不被替换为 `[REDACTED]` |
| 验证方式 | 构造含问号消息断言原样显示 |
| 预期收益 | 源A P1#66 |

#### 任务 S3.1.15：theme 导航常量

| 项 | 内容 |
|---|---|
| 位置 | `gui/delegates/theme.py:31-36` |
| 操作 | 固定行号 2/5/6 改 `NavIndex` 常量，导航调整不再 AssertionError |
| 验收标准 | 导航项调整不崩 |
| 验证方式 | 变更导航结构断言主题应用 |
| 预期收益 | 源A P1#64 |

#### 任务 S3.1.16：stealth_settings 公共属性

| 项 | 内容 |
|---|---|
| 位置 | `gui/views/stealth_settings.py:213-237` |
| 操作 | 改经 `_value/_set_value` 公共接口读写，移除私有直读写 |
| 验收标准 | 重构后行为不变且走公共接口 |
| 验证方式 | 设置/读取回归测试 |
| 预期收益 | 源A P1#70 |

#### 任务 S3.1.17：pdf_region 基序统一

| 项 | 内容 |
|---|---|
| 位置 | `pipeline_ops/pdf_region.py:42` |
| 操作 | `rule.page` 与 `extract_region` 统一 1 基（转换在边界），消除 off-by-one |
| 验收标准 | 指定第 3 页提取正确 |
| 验证方式 | 多页用例断言页命中 |
| 预期收益 | 源A P1#71 |

#### 任务 S3.1.18（并入 S3.1.2）：导航/向导/状态机常量化（余量）

> 已并入 S3.1.2。

#### 任务 S3.1.19：pdf_workbench rglob 后台化

| 项 | 内容 |
|---|---|
| 位置 | `gui/views/pdf_workbench.py:380-395` |
| 操作 | `rglob("*.pdf")` + `stat()` 移入后台线程，分批刷新 |
| 验收标准 | 深目录/大目录不冻结 UI |
| 验证方式 | 构造大目录断言界面响应 |
| 预期收益 | 源A P1#55 |

#### 任务 S3.1.20：step3 get_selections 后台化

| 项 | 内容 |
|---|---|
| 位置 | `gui/wizard/step3_fields.py:724` |
| 操作 | 同步 `get_selections()` 移入线程 + 无选择时超时兜底，不再永久假死 |
| 验收标准 | 无选择时窗口不假死 |
| 验证方式 | 打开选择器不操作断言可取消 |
| 预期收益 | 源A P1#54 |

#### 任务 S3.1.21：async_workers max_rows 参数

| 项 | 内容 |
|---|---|
| 位置 | `gui/async_workers.py:354` |
| 操作 | `CsvIndexWorker.__init__` 补 `max_rows` 参数或调用处移除，消除 TypeError |
| 验收标准 | CSV 索引调用不再 TypeError |
| 验证方式 | 调用断言成功 |
| 预期收益 | 源A P1#93 |

#### 任务 S3.1.22：design_system 字体缩放

| 项 | 内容 |
|---|---|
| 位置 | `gui/design_system.py:463-470,603,624-632` |
| 操作 | `apply_font_strategy` 保留 accessibility 缩放比例，不覆盖用户设置 |
| 验收标准 | 界面缩放（80-160%）对字体真实生效 |
| 验证方式 | 设置缩放断言字体 |
| 预期收益 | 源A P1#92 |

#### 任务 S3.1.23-24（并入 S3.1.2）：wizard/tray/导航簇

> 已并入 S3.1.2。

#### 任务 S3.1.25：AutosaveManager 后台写盘 + 失败提示

| 项 | 内容 |
|---|---|
| 位置 | GUI AutosaveManager |
| 操作 | 60s 全量写盘移入后台线程 + 失败可见提示（不再静默）；写盘间隔可配置 |
| 验收标准 | 主线程无周期写盘卡顿；写盘失败有提示 |
| 验证方式 | 采样主线程；构造写失败断言提示 |
| 预期收益 | 源B P2#94 |

#### 任务 S3.1.26：配置历史恢复先校验

| 项 | 内容 |
|---|---|
| 位置 | GUI 配置历史恢复 |
| 操作 | 恢复前先 validate，校验失败则不覆盖当前文件并报错 |
| 验收标准 | 恢复坏配置不破坏当前文件 |
| 验证方式 | 恢复坏配置断言当前文件不变 |
| 预期收益 | 源B P2#95 |

#### 任务 S3.1.27：switch_project 组件重建

| 项 | 内容 |
|---|---|
| 位置 | GUI switch_project |
| 操作 | 切换项目时重建/重指 task_runner/autosave/history/template_loader 路径，不再只改标签 |
| 验收标准 | 切换后各组件使用新项目路径 |
| 验证方式 | 切换项目断言组件路径 |
| 预期收益 | 源B P2#96 |

### 工作包 S3.2：孤儿代码接线 + 标注（方案 10，根因 7）

#### 任务 S3.2.1：接线类孤儿代码

| 项 | 内容 |
|---|---|
| 位置 | `safe_regex`→`validation.py`；`history_max_entries`→`task_history.py`；`assert_no_raw_hex`→CI；`ChangeDetector` 基线持久化；`AIBudget`/`validate_ai_output`/`ai_audit_record` 接入生产 |
| 操作 | ① `safe_regex` 接到 value_pattern；② `history_max_entries` 替代硬编码100（同时修复>100条删除旧记录问题）；③ `assert_no_raw_hex` CI 默认开启；④ ChangeDetector 内部基线持久化（解决 F665 哨兵假哈希）；⑤ AIBudget 等接入生产调用方；⑥ 变更历史内存有界 + save_rules 原子写 |
| 验收标准 | 各门禁/配置项有真实消费方；任务历史>100条不误删旧记录 |
| 验证方式 | 对每个接线点 grep 消费方；任务历史回归测试 |
| 预期收益 | 源A P1#69、源B P2#107/#112 + 根因 7 |

#### 任务 S3.2.2：标注类孤儿代码 + 消费方存在性测试

| 项 | 内容 |
|---|---|
| 位置 | `apply_to_playwright_context`/`ProxyRotator`/`AIGraphExtractor`/`archives.py` 等 |
| 操作 | ① 实验性组件加"实验性，不在主路径"标注；② `archives.py` 标记 deprecated；③ 补"消费方存在性"单测（对每个守卫/配置项 grep 消费方，零消费即红） |
| 验收标准 | 无未标注的零消费代码 |
| 验证方式 | 跑消费方存在性测试 |
| 预期收益 | 根因 7 + 防回归 |

#### 任务 S3.2.3：归档单实现 + 新增未知文件检测

| 项 | 内容 |
|---|---|
| 位置 | `fetching/archives.py` / `core/archive_security.py` / 完整性校验 |
| 操作 | 收敛两套归档安全实现为单一路径；完整性校验增加"新增未知文件"检测（清单比对），DLL 侧加载可见并告警 |
| 验收标准 | 唯一归档实现被生产调用；未知文件被检出 |
| 验证方式 | 注入未知文件断言告警；grep 归档调用点唯一 |
| 预期收益 | 源B P2#105/#106 |

### 工作包 S3.3：契约测试补齐（方案 11）

#### 任务 S3.3.1：CLI 输出快照驱动测试

| 项 | 内容 |
|---|---|
| 位置 | 新建 `tests/fixtures/cli_outputs/`；`tests/integration/test_cli_gui_contract.py` |
| 操作 | 5 套真实 `omnicrawl run` 快照（正常/失败/0条/异常/被拦网络）；断言 LogParser.parse_progress / parse_stats 输出匹配 |
| 验收标准 | GUI 进度条/统计面板解析真实输出正确 |
| 验证方式 | 跑新集成测试 |
| 预期收益 | 源B P2（GUI↔CLI 契约簇，进度条恒0%） |

#### 任务 S3.3.2：配置往返 e2e + 结构化证据测试

| 项 | 内容 |
|---|---|
| 位置 | 新建 `tests/integration/test_config_round_trip.py`、`test_validate.py` |
| 操作 | ① "GUI 配置 → CLI 跑产 N 条"往返测试；② 10 个拼写错误用例；③ export_single_record + 结构化证据路径参数化测试 |
| 验收标准 | 配置往返稳定；拼写错误全被拦截；证据路径有覆盖 |
| 验证方式 | pytest 集成测试 |
| 预期收益 | 源A P0#16 守护 + 源B P2 契约簇 |

### 工作包 S3.4：导出器缺陷簇

#### 任务 S3.4.1：导出器修正

| 项 | 内容 |
|---|---|
| 位置 | pipeline/exporters.py 等 |
| 操作 | ① records.jsonl 同一份数据不再写两遍；② CSV 列按字段定义顺序（或 schema）而非字母排序；③ parquet/duckdb 保留类型（不全 str 化）；④ 大表分批写出（不全量读内存）；⑤ responses.csv/errors.csv 受 outputs.csv 开关约束；⑥ xlsx 缺 openpyxl 显式告警；⑦ xlsx 行上限保护；⑧ 文件被占用时友好错误 |
| 验收标准 | 导出文件结构与字段顺序符合契约；内存可控；xlsx/CSV 细节符合预期 |
| 验证方式 | 大导出冒烟 + 输出校验 |
| 预期收益 | 源B P2#81/#82/#83/#84/#85 + 源A P1#46 + P3#127 |

### 阶段 3 退出条件（回归门禁）
- [x] `tests/integration/` 全绿
- [x] 无 GUI 线程冻结（手动验收清单通过）
- [x] 消费方存在性测试通过
- [x] CLI 快照驱动契约测试通过
- [x] 导出器输出校验通过
- [x] 源B P2 剩余项 + 源A P1 GUI 类全部勾销

---

## 5. 阶段 4：系统性加固（持续推进）

**目标：** 包根惰性化、默认路径治理、破坏性命令防护 + i18n + 打包修复、pdfx 样板反向输出、P3 批量清理，落地方案 12/13/14/15。

**任务来源：** 根因 8/9/10 + "假语言包" + 源A P0#23 后项 + 源B P3 批量。

### 工作包 S4.1：包根惰性化（方案 12，根因 8）

| 项 | 内容 |
|---|---|
| 位置 | `omnicrawl/__init__.py:192` 等 |
| 操作 | ① 删 `_setup_compat_aliases()` 调用；② `__getattr__` 真正接管惰性重定向；③ 补 `MetaPathFinder` 处理子模块形式；④ import 失败改 `logger.warning`；⑤ 加启动耗时 CI 断言（<50ms 阈值）；⑥ pipeline 模块级重量级 fetcher/extractor import 下放（纯 HTTP 任务不全量加载） |
| 验收标准 | `import omnicrawl` 从 287ms 降至 ms 级；`--version` 显著提速 |
| 验证方式 | 计时脚本对比；CI 断言 |
| 预期收益 | 根因 8 + 源B P2#77 + P3 启动耗时簇 |

### 工作包 S4.2：默认路径治理（方案 13，根因 9）

| 项 | 内容 |
|---|---|
| 位置 | CLI `--config`、pdfx 默认路径、GUI cwd 候选、`load_config` root 探测 |
| 操作 | ① `--config` default 改 cwd 相对或 required；② pdfx input/work/database 默认 `<user_data_dir>/pdfx/`；③ 启动第一行日志打印 data_dir/config_path/database；④ `--legacy-data-dir` 兼容旧行为；⑤ GUI 去 cwd 候选改 application_dir()；⑥ `load_config` root 探测固定策略（不再跨环境漂移） |
| 验收标准 | 三种环境（源码/pip/PyInstaller）下默认路径一致且不指向安装目录 |
| 验证方式 | 三环境分别启动断言路径 |
| 预期收益 | 根因 9 + 源B P2#63/#90 + P3 默认路径簇 |

### 工作包 S4.3：破坏性命令防护 + i18n 修复 + 打包（方案 14）

#### 任务 S4.3.1：破坏性命令统一防护

| 项 | 内容 |
|---|---|
| 位置 | `core/safe_action.py`；reset/reset_stage/rollback-config |
| 操作 | 全部接入 `require_explicit_apply`；未知 stage/action 显式 `raise ValueError` 替代静默 no-op |
| 验收标准 | 无 --apply 不执行删除；未知 stage 报错 |
| 验证方式 | 命令行冒烟 |
| 预期收益 | 根因 10 + 源B P2#101 |

#### 任务 S4.3.2：i18n 链路修复（"假语言包"）

| 项 | 内容 |
|---|---|
| 位置 | `gui/i18n.py:56`、spec、locale/ |
| 操作 | ① domain 统一 `omnicrawl-gui`；② spec datas 加 `locale/`；③ 生成缺失 .mo 并纳入打包；④ CI 加 i18n gate（中文字面量非 `_()` 包裹则红） |
| 验收标准 | 切换语言后界面真实切换 |
| 验证方式 | 切换 en_US 断言界面文本 |
| 预期收益 | 源A P1#102 + "假语言包" |

#### 任务 S4.3.3：打包/启动脚本修复（第一批）

| 项 | 内容 |
|---|---|
| 位置 | `tools/prepare_windows_runtime.ps1:20-71`、`packaging/OmniCrawler.spec:77`、Launcher |
| 操作 | ① PS1 顶部定义 `$KNOWN_SHA256`，无 expected 即 fail；② spec 启用 `windowed_traceback`；③ Launcher 补日志路径提示 |
| 验收标准 | 第三方二进制有完整性校验；便携版崩溃有 traceback/日志提示 |
| 验证方式 | 完整跑一次构建 + 冒烟 |
| 预期收益 | 源A P1#103/#108 + P3 打包簇 |

#### 任务 S4.3.4：打包/启动脚本簇（第二批）

| 项 | 内容 |
|---|---|
| 位置 | `tools/add_template_version.ps1:1`、`build_windows.ps1:209`、`install_windows.ps1:17`、全部 .bat、`run_*.bat` |
| 操作 | ① add_template_version 去除硬编码其他项目绝对路径，改为仓库相对路径；② build/install 加 CPU 位数/架构断言，`py -3` 显式版本；③ .bat 打包前 CRLF 转换 + 行为统一（run_workbench 转发 `%*`）；④ prepare_windows_runtime 带 BOM UTF-8 兼容 PS5.1 |
| 验收标准 | 打包流程可复现；架构错误早暴露；bat 行为一致 |
| 验证方式 | 完整构建 + 三 bat 冒烟 |
| 预期收益 | 源A P1#104/#105/#106/#107 |

### 工作包 S4.4：pdfx 样板反向输出（方案 15）

| 项 | 内容 |
|---|---|
| 位置 | 见各迁移点 |
| 操作 | ① extraction.py 每线程 DB → parser.py（S1.5.1 已做）；② `safe_regex` → validation.py；③ `exporter.py:safe_cell` → pipeline/exporters.py 与 extraction/extractors.py:337（裸 json.loads）；④ ingest D45 路径+大小命中跳过 SHA → 补 SHA-256 校验（services/workspace.py:74-94 多 GB 工作区整读 RAM）；⑤ 写"子系统间整改样板迁移"清单 |
| 验收标准 | 每个迁移点有对应测试；workspace 不再整读多 GB 文件；同路径同大小新文件被 SHA 检出 |
| 验证方式 | 逐迁移点跑目标模块测试 |
| 预期收益 | 源A P0#13 + P1#87、源B P1#50 + F699 等 |

### 工作包 S4.5：P3 批量清理（可选，按批次）

| 项 | 内容 |
|---|---|
| 操作 | 按"白名单样板"（§7）批量清理：源B P3#126-156 全部 + 源A 各 low/ux 项、孤儿代码删除、注释/缩进/类型注解修正、assert 改显式 raise、import 提顶等 |
| 验收标准 | 各文件 low/ux 项清零或标注；P3 逐条勾销 |
| 验证方式 | 逐文件复核 |
| 预期收益 | 源B P3#126-156（31 条）+ 源A P3 500+ 项 |

### 阶段 4 退出条件
- [x] `import omnicrawl` 耗时达标
- [x] 三环境默认路径一致
- [x] i18n 切换生效
- [x] 打包冒烟通过
- [x] 全量回归绿

---

## 6. 覆盖矩阵（问题 → 阶段 → 任务）

> **全覆盖注册表**：两套审计源（文件A 终版 P0#1-24 / P1#25-110，文件B 问题清单 P0#1-15 / P1#16-57 / P2#58-125 / P3#126-156）的**逐条**映射、去重对照与 46 条新增任务明细见 `docs/OPTIMIZATION_PLAN_TRACKING.md`。覆盖统计：**A 110/110、B 156/156，100%**。

### 6.1 P0（24 条合并编号，阶段 1 全覆盖）

| P0# | 问题 | 阶段 | 任务 |
|---|---|---|---|
| 1 | 价格正则 $ 锚点 | S1 | S1.4.1 |
| 2 | deep_merge 浅拷贝 | S1 | S1.1.1 |
| 3 | 无文件日志 | S1 | S1.1.2 |
| 4 | lxml.html API 误用 | S1 | S1.1.3 |
| 5 | headless 引用未定义 _() | S1 | S1.1.4 |
| 6 | QThread 销毁崩溃 | S1 | S1.1.5 |
| 7 | PDF 共享 DB 多线程事务 | S1 | S1.5.1 |
| 8 | max_pages=0 被 or 吞 | S1 | S1.2.3 |
| 9 | max_pages 只计成功页 | S1 | S1.2.4 |
| 10 | 通用异常缺 drain | S1 | S1.2.1 |
| 11 | SSE 忙循环 | S1 | S1.4.3 |
| 12 | 归档 IndexError | S1 | S1.3.2 |
| 13 | 模板合并永远失败 | S1 | S1.4.2 |
| 14 | reprocess 记录构造在 try 外 | S1 | S1.2.2 |
| 15 | Pipeline __init__ 不回滚 | S1 | S1.5.2 |
| 16 | 自动配置契约不匹配 | S1 | S1.4.5 |
| 17 | 字段名插入 XPath 崩溃 | S1 | S1.4.4 |
| 18 | SSRF/重绑定 + 内存耗尽 | S1 | S1.3.5 + S1.3.6 |
| 19 | requires-python 3.10 vs 3.11 API | S1 | S1.5.3 |
| 20 | EasySpider scroll 崩溃 | S1 | S1.5.7 |
| 21 | 模板监控 None content_type | S1 | S1.5.6 |
| 22 | async 跨 loop | S1 | S1.5.8 |
| 23 | commands 导出不存在名称 | S1 | S1.5.4 |
| 24 | Redis frontier 计数/原子 | S1 | S1.5.5 |

### 6.2 P1 关键项（28 条合并编号）

> 合并编号对应源A P1#25-52 与源B P1#16-57 的交叉去重；**两源全量逐条映射见 TRACKING.md §3-4**。

| P1# | 问题 | 阶段 | 任务 |
|---|---|---|---|
| 25 | safe_regex ReDoS | S4 | S4.4.2 |
| 26 | request_payload 明文 | S1 | S1.3.3 |
| 27 | headers 广播子资源 | S1 | S1.3.4 |
| 28 | 插件审批门绕过 | S1 | S1.3.7 |
| 29 | CSV 注入前缀绕过 | S1 | S1.3.1 |
| 30 | crawl4ai 绕过 Egress | S2 | S2.5.5 |
| 31 | 密钥前缀拼写 | S1 | S1.3.8 |
| 32 | secrets 明文链 | S2 | S2.2.1 + S2.2.2 |
| 33 | env_checker 冻结60s | S3 | S3.1.1 |
| 34 | pip 安装冻结120s | S3 | S3.1.1 |
| 35 | 运行历史错记录 | S3 | S3.1.3 |
| 36 | 导航错页 | S3 | S3.1.2 |
| 37 | PDF 工作台取消卡死 | S3 | S3.1.6 |
| 38 | Toast 鼠标死区 | S3 | S3.1.4 |
| 39 | 日志内存无限增长 | S3 | S3.1.4 |
| 40 | 任务历史误删 | S3 | S3.2.1 |
| 41 | _AIEnrichWorker 崩溃 | S1 | S1.1.5 |
| 42 | 无托盘静默停止 | S3 | S3.1.5 |
| 43 | .env 优先级反 | S2 | S2.1.3 |
| 44 | 金额单位误判 | S2 | S2.5.1 |
| 45 | retry 配置不生效 | S2 | S2.1.4 |
| 46 | partial 伪装成功 | S2 | S2.4.1 |
| 47 | reprocess 导出陈旧 | S2 | S2.5.2 |
| 48 | OCR 语言不兼容 | S2 | S2.3.3 |
| 49 | 阶段隔离不完整 | S2 | S2.3.4 |
| 50 | i18n 链路失效 | S4 | S4.3.2 |
| 51 | state_store 非原子 | S2 | S2.5.3 |
| 52 | 流式参数丢弃 | S2 | S2.5.4 |

### 6.3 P2/P3 系统性簇

| 簇 | 阶段 | 任务 |
|---|---|---|
| 配置契约双向失配 | S2 | S2.1.1 |
| GUI↔CLI 契约零测试 | S3 | S3.3.1 |
| secrets 五处明文 | S2 | S2.2.2 |
| 16 处孤儿代码 | S3 | S3.2.1 + S3.2.2 |
| 包根 eager 加载 | S4 | S4.1 |
| 默认值指向安装目录 | S4 | S4.2 |
| 导出器缺陷簇 | S3 | S3.4.1 |
| 配置错误信息差 | S2 | S2.1.2 |
| 重试配置双轨 | S2 | S2.1.4 |
| 压缩解码不全 | S2 | S2.5.6 |
| 假绿灯（校验/0条） | S2 | S2.1.1 + S2.4.1 |
| 假指纹（指纹不含 headers） | S2 | S2.5.5 |
| 假 AI（启发式） | S2 | S1.4.5 |
| 假校验（代理验证/配置 diff） | S2 | S2.1.1 |
| 假语言包 | S4 | S4.3.2 |

### 6.4 根因 → 方案 → 阶段

| 根因 | 主方案 | 阶段 |
|---|---|---|
| 1 浅拷贝与可变默认值 | 方案1 | S1 |
| 2 GUI 主线程阻塞 | 方案9 | S3 |
| 3 异常隔离不彻底 | 方案2 | S1 |
| 4 安全边界不完整 | 方案3/6 | S1+S2 |
| 5 日志可观测性缺失 | 方案1 | S1 |
| 6 配置契约失配 | 方案5 | S2 |
| 7 孤儿代码零消费 | 方案10 | S3 |
| 8 包根 eager 加载 | 方案12 | S4 |
| 9 默认值指向安装目录 | 方案13 | S4 |
| 10 破坏性操作零防护 | 方案14 | S0+S4 |

### 6.5 方案 → 阶段

| 方案 | 阶段 | 覆盖任务 |
|---|---|---|
| 1 崩溃三件套 | S1 | S1.1.1-5 |
| 2 pipeline 异常隔离 | S1 | S1.2.1-5 |
| 3 安全边界闭合 | S1 | S1.3.1-8 |
| 4 正则与字段识别 | S1 | S1.4.1-5 |
| 5 配置单一真源 | S2 | S2.1.1-4 |
| 6 secrets 统一加密 | S2 | S2.2.1-4 |
| 7 PDF 容错 | S2 | S2.3.1-7 |
| 8 0 条语义 | S2 | S2.4.1 |
| 9 GUI 线程模型 | S3 | S3.1.1-27 |
| 10 孤儿代码接线 | S3 | S3.2.1-3 |
| 11 契约测试 | S3 | S3.3.1-2 |
| 12 包根惰性化 | S4 | S4.1 |
| 13 默认路径治理 | S4 | S4.2 |
| 14 破坏性命令+i18n+打包 | S4 | S4.3.1-4 |
| 15 pdfx 样板反向输出 | S1+S4 | S1.5.1 + S4.4 |

### 6.6 新增任务注册（全覆盖补齐，明细见 TRACKING.md §9）

- **S1 后项**：S1.3.2（归档穿越/半截文件/大小写/纯"."）—— 已并入 S1.3.2 正文。
- **S2 新增**：S2.2.3、S2.2.4、S2.3.7、S2.5.7-48（seed 保留 / CookieSession / Retry-After / SPA 检测 / 超时取消 / Selenium / 代理 context / extractors 容错 / fail_closed / workspace 流式 / offline_demo / scheduler / recovery / execution_backend / doctor / --ir / fingerprint / auto_pilot / resources / markdown_exporter / stealth / quality_report / 主题匹配 / TableProcessor / 阶段归类 / discover_links / export 空库 / run_finished / 增量统计 / retry_failed 分页 / sdk.run / 指纹缓存 / 插件加载 / StateStore 关闭 / wait 超时 / 调度租约 / fetcher 关闭 / long_poll / InProcessBackend / 限速器统一）—— 全部在本章工作包内有完整表格。
- **S3 新增**：S3.1.7-27、S3.2.3 —— 全部在本章工作包内有完整表格。
- **S4 新增**：S4.3.4 —— 在本章工作包内有完整表格。

---

## 7. 质量白名单（整改样板，按此标准验收）

来自报告第八节，作为各阶段"改到多好算达标"的参照：

1. **pdfx/\*（22 文件）** —— D 系列整改注释与代码一致；safe_regex / safe_cell / validate_runtime_config / 阶段隔离 / SHA-256 跳过为全项目标杆。
2. **pipeline_ops/\*（9 文件）** —— preflight.py 是"消费 validate_config 返回值"的样板。
3. **gui/views/pdf_workbench.py** —— C50 外发确认、D14 OCR 降级询问、D18 持久工作目录。
4. **gui/widgets/** 设计令牌全覆盖组件。
5. **核心库** —— core/errors、logging_utils、models、run_state、runtime_paths、pipeline/core、task_ir、provenance、pdfx/database、pdfx/exporter。
6. **安全修复** —— egress 拒绝路径、zip 防护、模板穿越、SHA256SUMS 篡改检测、归档整体验证。
7. **全项目质量最高函数** —— `research_package.py:restore_package`（四道防线）、`env_checker.py:check_omnicrawl`（分类处理+短超时）、`error_dialog.py` 脱敏错误对话框。

> 验收原则：任何修复若低于白名单对应能力水平，视为未完成。

---

## 8. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| 配置白名单 strict 引爆存量配置 | 存量配置校验失败 | 默认关闭 --strict；双向收敛（既补校验也修注入路径） |
| 0 条即非 0 破坏脚本 | 依赖 rc 的脚本行为变化 | --strict 默认关闭；文档注明 |
| 包根惰性化暴露同名映射冲突 | 子模块导入歧义 | MetaPathFinder 显式解析顺序；阶段4单独评审 |
| 默认路径迁移破坏旧脚本 | 找不到数据 | --legacy-data-dir 兼容 |
| GUI 线程改造回归 | 界面竞态 | 逐点改造 + 手动验收清单；AsyncWorkerManager 复用 |
| secrets 加密引入密钥管理 | 密钥丢失 | OS keyring 优先；密码派生 fallback；导出前明文扫描兜底 |
| 新增任务量大引入回归 | 阶段2/3 范围膨胀 | 按工作包独立提交；每个工作包带验收测试；阶段门禁逐条勾销 |

**回滚策略：** 每个阶段工作包独立提交；`safe_action.py` 保证删除可回退；阶段4前所有阶段有 git tag 可回滚点。

---

## 9. 验收总纲（最终发布门禁）

- [ ] P0 24 条全部勾销，P1 28 条（关键项）全部勾销
- [ ] **全覆盖注册表 `docs/OPTIMIZATION_PLAN_TRACKING.md`：源A 110/110 + 源B 156/156 逐条勾销**
- [ ] 10 根因全部有对应修复证据
- [ ] 15 方案全部落地
- [ ] `pytest tests/` 全绿，覆盖率不低于阶段0基线
- [ ] `tests/integration/` 全绿（含新增契约/往返/消费方测试）
- [ ] ruff / mypy / 覆盖率门禁通过
- [ ] 手动验收：CLI run / GUI 全流程 / PDF 并发 / 模板合并 / 多语言切换 / 打包冒烟
- [ ] 3.10 环境 import 通过（或明确 requires-python >=3.11）
- [ ] 无明文凭据落盘审计通过
