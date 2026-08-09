# 安全与合规

> 本文档整合了 OmniCrawler 的安全不变量门禁、网络安全模型和合规要求。

## 技术默认保护

- 仅 HTTP/HTTPS；流式协议必须显式配置。
- URL 文本与 DNS 解析结果均拒绝本机、私网、链路本地和保留地址。
- 重定向和浏览器子请求重复执行目标检查；TLS 默认校验。
- robots 默认遵守且失败关闭；每主机限速、有限重试和 Retry-After。
- 页面数、深度、响应、robots、浏览器接口捕获、运行时长、工作区和磁盘余量有限额。
- ZIP/TAR 安全解压防路径穿越、链接、重复路径、超大文件和压缩炸弹。
- 文件名规范化、路径 containment、原子写入、Excel 公式注入防护。
- 插件路径、API 版本和声明权限预检；未批准权限不执行。
- 凭据使用环境变量或 keyring；日志、诊断和 summary 脱敏。

## 网络安全模型

### 默认边界

- 只允许 HTTP 和 HTTPS；
- 默认阻止本机、私网、链路本地、保留、多播和未指定地址；
- DNS 返回的任一地址不安全时，整个目标被拒绝；
- 每一跳重定向重新执行目标检查；
- robots.txt 使用与普通 HTTP 请求相同的安全路径；
- 直连使用已批准的地址字面量建立 Socket，同时保留原始 Host 和 TLS SNI；
- 未配置代理时忽略系统和环境代理，防止策略被隐式绕过。

### DNS 重绑定防护

安全检查和 Socket 连接不能分别解析域名。连接时取得经过策略批准的地址集合，随后只对
其中的地址字面量建立连接。即使攻击者在前后 DNS 查询间改变答案，私网答案也会在连接前被拒绝。

### 显式代理

配置 `http.proxy` 表示用户明确授权该代理作为可信网络边界：

- 代理自身的 URL 和解析地址仍接受安全策略检查；
- 到代理的 Socket 连接固定到已批准地址；
- HTTP 目标和每次重定向的 URL 仍接受协议与范围检查；
- 目标域名实际连接地址由代理解析和控制，OmniCrawler 不能在本机证明代理侧的 DNS 结果。

需要严格隔离时，应使用组织管理的代理，并在代理侧同步禁止私网、云元数据和未授权端口。

### 明确例外

`allow_private_network: true` 和 `resolve_dns: false` 会降低默认保护，只应对用户拥有或明确获权的
内网站点启用。配置、发行说明和诊断包必须保留这些例外的审计证据。

## 统一 Egress Broker

Egress Broker 将同步/异步 HTTP、每次重定向、robots、登录、附件、Playwright 页面与子请求、SSE、
WebSocket、AI、插件和外部存储统一收口。出口同时执行网络目标策略、凭据作用域、请求/流量/并发/
时间/费用预算、每主机熔断、任务停止与全局紧急断网检查。插件只获得与声明域名、用途和请求预算绑定
的能力令牌及受控客户端。

> 2.2.0+ 优化：Egress Broker 默认开启且不可关闭，含凭据作用域和熔断。

`<workspace>/logs/egress-audit.jsonl` 是脱敏追加日志；敏感查询参数和 URL 用户信息只记录为安全形式，凭据头只记录
字段名。审计目录不可写或磁盘已满时，出口策略、预算和停止开关仍继续生效，运行摘要的 `egress_audit.write_failures` 明确标记缺失的审计证据，不能将该任务视为审计完整。`omnicrawl security-report -c <config>` 可列出实际访问的协议、主机、端口、用途、插件主体、
拒绝次数和 SDK 传输例外。

### 浏览器与外部 SDK 明确例外

Playwright 路由会检查页面、XHR/fetch、Service Worker 可见请求和下载相关网络活动。当前 Selenium
BiDi 组合在实际 Chrome 中存在拦截死锁，因此默认安全关闭；显式启用未拦截兼容模式会留下审计边界。
S3、OpenSearch 与 PostgreSQL SDK 在调用前经过同一出口策略和预算，但最终 Socket 由 SDK 控制，
审计事件会标记 `sdk_controls_final_socket`，部署时仍建议配合受管代理或主机防火墙。

## 安全不变量门禁

| 编号 | 不变量 | 实现边界 | 自动证据 |
|---|---|---|---|
| INV-001 | 未批准 URL 不进入传输层 | `EgressBroker.authorize/request`、安全 opener、Playwright route | `test_egress_v120.py`、`test_security_regressions.py` |
| INV-002 | 凭据只发送到批准域名和用途 | `credential_domains/purposes` | `test_credentials_are_bound_to_domain_and_purpose` |
| INV-003 | 已停止任务不再联网 | `RunControl` + Egress stop/kill switch | `test_task_global_switches_and_stopped_run_are_fail_closed` |
| INV-004 | 重处理不覆盖原始证据 | raw archive 按响应哈希归档、派生阶段重置 | `test_v110_features.py` 重处理/原始归档测试 |
| INV-005 | 人工修改不伪装为原始抽取 | 修订表与审计事件独立于原始记录 | `test_quality_review.py` 人工编辑审计测试 |
| INV-006 | 配置迁移保留未知字段 | 深合并、迁移与 GUI 往返保留扩展段 | `test_migrations.py`、`test_compatibility_v112.py` |
| INV-007 | 非幂等导出不重复提交 | `export_commits` 提交锁和稳定幂等键 | `test_run_reliability_v120.py` |
| INV-008 | 删除需连续确认 | `updates.confirm_missing_runs` 及变化追踪 | `test_v110_features.py` 连续缺失测试 |
| INV-009 | 状态机、故障注入验证全部规则 | 七态穷举、崩溃恢复、预算/熔断/插件越权测试 | `test_run_reliability_v120.py`、`test_egress_v120.py` |

每次发布必须运行全量测试、上述专项文件、真实浏览器门禁、Mypy、Ruff、源码编译及
分组覆盖率门禁。外部 SDK 最终 Socket 与 Selenium 未拦截兼容模式属于明确例外，必须在安全报告中
可见，不能描述为已经实现 DNS 固定或逐请求拦截。

## 合规决策由部署方完成

- 数据源是否公开，或是否具有合同、账号和 API 授权。
- 服务条款、robots、API 速率和缓存要求。
- 版权、数据库权、商业秘密和再分发许可证。
- 个人信息最小化、处理依据、告知、跨境、保留和删除要求。
- 员工账号、Cookie、Token 的访问控制与轮换。

模板的 `license` 只是提示，不替代法律审查。网页公开可访问也不自动意味着可以复制或再分发全部内容。

## 禁止范围

项目不实现验证码破解、付费墙绕过、反爬规避、身份伪装、账号接管、权限提升或未授权内网探测。遇到挑战页应降低速率、改用官方 API、联系站点或停止任务。

## 事件响应

发现范围错误或敏感数据时：停止任务，保存 run_id 和审计，撤销/轮换凭据，隔离输出，评估通知义务，修正范围后用小样本复验。不要为了调试把 Cookie/Token 上传到 issue 或诊断包。
