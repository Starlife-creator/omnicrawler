# OmniCrawler 0.6.0 配置参考

配置为 UTF-8 YAML。未知顶层字段会在迁移和 GUI 往返保存时保留。相对路径以配置文件所在项目根目录解析。

## 最小配置

```yaml
config_version: 5
project: {name: demo, workspace: work/demo}
source: {kind: static_html, seeds: [https://example.org/]}
http:
  user_agent: "DemoCrawler/1.0 (+contact: owner@example.org)"
  respect_robots: true
extract: {mode: auto, fields: {}}
outputs: {jsonl: true, csv: true, xlsx: true}
```

## project

- `name`：任务名。
- `workspace`：状态库、原始响应、附件、诊断与结果目录。

## source

- `kind`：`static_html`、`crawl`、`focused`、`incremental`、`url_list`、`rest`、`graphql`、`form`、`sitemap`、`feed`、`browser`、`file`、`media`、`websocket`、`sse`、`long_poll`、`redis`、`scrapy`，或插件注册名称。
- `seeds`：URL 字符串，或含 `url/method/headers/payload/render/kind` 的对象。
- `method/headers/params/payload/content_type`：API 或表单请求参数。
- `pagination`：页码型使用 `type: page`、`parameter/start/end`；服务端 next URL 使用 `next_path`。
- `query/query_file/variables`：GraphQL。
- `login`：`url/method/content_type/fields/headers`，先登录再复用 Cookie。
- `max_messages/duration_seconds/subscribe`：流式来源的硬边界。

## crawl

- `strategy`：`bfs`、`dfs`、`priority` 或 `random`。
- `max_pages/max_depth/concurrency`：任务硬上限。
- `same_host/allow_domains/allow_patterns/deny_patterns`：范围控制。
- `focus_keywords`：根据 URL 与锚文本计分。

## http

- `user_agent`：应含真实维护者联系方式。
- `timeout_seconds/retries/delay_seconds`：超时、重试与每主机间隔。`retries` 为总尝试次数（0 表示不重试，仍会尝试 1 次）。
- `retry_base_seconds/retry_max_seconds/retry_jitter`：指数退避。
- `retry_on_status`：发生重试的状态码列表，默认 `[408, 425, 429, 500, 502, 503, 504]`；设为空数组 `[]` 表示不重试任何 HTTP 状态码。
- `retry_max`：旧轨兼容别名，等价 `retries`，仅在未写 `retries` 时生效（非负整数，0 表示不重试）。
- `respect_robots/robots_fail_closed/robots_cache_ttl_seconds/robots_max_bytes`。
- `verify_tls/max_redirects/max_response_bytes`。
- `allow_private_network`：默认 `false`；仅对自有或已授权内网站点开启。
- `resolve_dns/dns_fail_closed/dns_cache_ttl_seconds`：DNS 结果安全检查。
- `auto_browser_fallback`：检测到空壳动态页面时升级到浏览器。
- `engine`：`urllib` 或 `httpx_async`。
- `headers/proxy`：全局请求头和单个授权代理。未填写 `proxy` 时不会继承环境代理变量；显式代理
  被视为可信网络边界，OmniCrawler 验证并固定代理地址，但目标域名的最终解析由代理负责。

## browser

- `engine`：`playwright` 或 `selenium`。
- `headless/pool_size/wait_until`。
- `actions`：`wait_for`、`click`、`fill`、`press`、`scroll_bottom`、`wait_ms`。
- `capture_api_responses`：捕获 XHR/fetch JSON。
- `max_api_response_bytes/max_api_capture_bytes`：单响应与单页面总捕获上限。

## session 与 auth

```yaml
session: {persist_cookies: false, name: default}
auth:
  provider: my_auth_plugin
  options: {audience: example}
```

`auth.provider` 在每次请求前调用插件。配置内密钥使用完整值 `secret://name`；运行时从 `OMNICRAW_SECRET_NAME` 或系统 keyring 读取。

## extract

- `mode`：`auto/html/json/table/text` 或 processor 插件名。
- `parser/extractor`：独立 parser/extractor 插件；不可同时指定。
- `parser_options/extractor_options`：插件选项。
- `item_selector`：HTML 重复项 CSS 选择器。
- `item_path`：JSON 数组路径，支持点号、数字下标和 `[*]`。
- `quality_threshold/review_low_confidence`。

HTML 字段规则：

```yaml
extract:
  mode: html
  item_selector: article
  fields:
    title:
      selectors: [h1, h2.title, "meta[property='og:title']"]
      attr: content
      required: true
      transforms: [normalize_space]
    price:
      xpath: ".//span[contains(@class, 'price')]"
      regex: "([0-9.,]+)"
      data_type: money
    canonical:
      source: opengraph
      property: url
    schema_name:
      source: jsonld
      path: name
    api_value:
      source: browser_response
      url_pattern: "/api/items"
      path: "items.0.value"
```

通用属性包括 `selector/selectors/xpath/attr/all/join/regex/group/default/transforms`。每个字段保存命中路径、原始值、清洗值、来源 URL 和置信度。

JSON 字段使用 `selector` 或 `path` 与 `type: jsonpath`。质量属性可使用：

- `required`、`required_if`（`equals/in/present`）。
- `data_type`：`string/integer/number/money/date/datetime/enum`。
- `pattern/min_length/max_length/min/max/values`。
- `cross_field`：`equals/not_equals/gt/gte/lt/lte`。
- `duplicate_key` 与 `anomaly`（字段、z-score、最小样本）。

## transformers

```yaml
transformers:
  - name: normalize_company
    options: {dictionary: data/companies.csv}
```

按配置顺序在质量检查和存储前执行；插件可返回修改后的记录或字典。

## download 与 incremental

- `download.enabled/extensions/media`：附件扩展名白名单和媒体下载。
- `incremental.skip_unchanged`：内容哈希不变时跳过派生阶段。
- `incremental.archive_raw`：保留原始响应；阶段级 `reprocess` 依赖此项。

## processors.pdf

- `enabled`：采集后运行 PDF 流水线。
- `config`：首次创建 PDF 子项目时使用的字段模板。
- `project_config`：已有 PDF 子项目配置；留空则创建 `<workspace>/pdf/project.yaml`。
- `skip_ocr/ocr_backend`。

PDF 子项目的 parser、OCR、retrieval、extraction、normalization、validation 和 fields 详见生成配置中的注释及用户指南。

## outputs

```yaml
outputs:
  jsonl: true
  csv: true
  xlsx: true
  parquet: false
  duckdb: false
  exporter: default
  plugin_exporters: [warehouse]
  exporter_options:
    warehouse: {table: records}
```

主 exporter 失败会使任务失败；额外 exporter 是否失败开放由 `plugins.fail_open` 决定。

## storage

```yaml
storage:
  objects:
    backend: local        # local 或 s3
    local_directory: .
    bucket: ""
    prefix: omnicrawl
  records:
    backends:
      - {kind: postgresql, dsn: "secret://postgres_dsn", table: omnicrawl_records}
      - {kind: opensearch, hosts: [https://search.example], index: omnicrawl}
    fail_open: true
    max_errors: 200
  retention:
    raw_days: 30
    artifacts_days: 90
    diagnostics_days: 14
```

SQLite 始终是本地恢复权威；外部后端是记录镜像。`max_errors` 限制摘要中保留的最近失败数（`0` 表示不保留样本），但 `storage.error_counts` 始终记录每个镜像后端的完整失败计数，避免长任务因重复故障无限占用内存。错误样本同时保留在兼容字段 `storage_warnings`。

## resources

- `minimum_free_disk_bytes`：磁盘低于此值安全停止，默认 512 MiB。
- `maximum_runtime_seconds`：0 表示不限制。
- `maximum_workspace_bytes`：0 表示不限制。
- `check_interval_seconds`：资源检查间隔。

## egress（1.2.0统一网络出口）

```yaml
egress:
  enabled: true
  allowed_schemes: [http, https, ws, wss]
  allowed_ports: []             # 空表示按协议默认；否则只允许列出的端口
  allowed_domains: []           # 空表示沿用任务目标策略
  credential_domains: []        # 空时从入口、登录、AI与存储端点推导
  credential_purposes: [fetch, login, redirect, robots, browser, stream, ai, storage, plugin]
  maximum_requests: 0           # 0表示不限
  maximum_bytes: 0
  maximum_concurrency: 0
  maximum_runtime_seconds: 0
  maximum_cost: 0
  circuit_failure_threshold: 5
  circuit_recovery_seconds: 30
  audit: true
  allow_unintercepted_selenium: false
  experimental_selenium_bidi_guard: false
```

默认使用 Playwright 对浏览器每个子请求执行出口检查。Selenium 在当前兼容矩阵中不能稳定证明最终
子请求拦截，因此默认拒绝启动；`allow_unintercepted_selenium` 是显式降级边界，会进入审计，
不应在不可信目标上启用。用 `omnicrawl security-report -c <config>` 查看访问边界。

## plugins

```yaml
plugins:
  paths: [plugins/site.py]
  allow_external_paths: false
  approved_permissions: [network]
  fail_open: false
  hook_fail_open: true
```

路径默认必须位于项目目录。生命周期事件包括 `before_run/before_fetch/after_fetch/after_extract/before_export/after_export/after_run/on_error/before_reprocess/after_reprocess`。

## 环境变量与迁移

- `${NAME}` 与 `${NAME:-default}` 在加载配置时展开。
- `secret://name` 只解析完整字符串，不支持拼接。
- 2.x/3.x 配置会在内存中迁移并产生迁移说明；用 `omnicrawl migrate` 输出新文件，原文件不会覆盖。
