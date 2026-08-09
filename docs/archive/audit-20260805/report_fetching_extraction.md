# 审查报告: fetching/extraction

> 审查范围: `src/omnicrawl/fetching/` 12 个文件 + `src/omnicrawl/extraction/` 8 个文件，共 20 个文件，约 4730 行。
> 语法检查: 全部文件 `python -m py_compile` 通过，无编译失败。
> 方式: 逐行人工审查；并交叉核对了 `security/egress.py`、`security/policy.py`、`core/errors.py`、`core/models.py`、`core/config.py`、`core/utils.py`、`runtime/resource_profiles.py`、`services/ai_safety.py` 以确认假设。

## 汇总（按严重级别计数）

| 严重级别 | 数量 |
|---------|------|
| critical | 2 |
| high | 9 |
| medium | 31 |
| low | 34 |
| ux | 7 |
| **合计** | **83** |

分文件统计（cr / hi / me / lo / ux）：

- fetching/__init__.py: 0（空包文件，无问题）
- fetching/action_recorder.py: 0/0/3/4/1
- fetching/archives.py: 0/0/1/6/0
- fetching/async_fetcher.py: 1/1/4/4/1
- fetching/browser_fetcher.py: 1/3/6/8/1
- fetching/captcha_ocr.py: 0/0/0/3/1
- fetching/http_client.py: 0/0/6/6/1
- fetching/retry.py: 0/0/1/3/0
- fetching/routing.py: 0/1/1/2/0
- fetching/session.py: 0/0/1/3/0
- fetching/stealth_enhanced.py: 0/1/4/6/0
- fetching/streams.py: 0/0/4/4/1
- extraction/__init__.py: 0/0/1/0/0
- extraction/ai_graph.py: 0/0/4/6/1
- extraction/api_discovery.py: 0/0/2/5/0
- extraction/extractors.py: 0/1/4/7/0
- extraction/field_designer.py: 0/1/1/3/0
- extraction/html_tools.py: 0/0/3/4/0
- extraction/intelligent_scraper.py: 0/0/3/7/0
- extraction/topic_filter.py: 0/0/2/3/0

## 问题清单

### [critical] fetching/async_fetcher.py:48-55, 112-114 - 无 DNS 固定且代理未过策略，存在 SSRF/DNS 重绑定绕行

- 现状: `_ensure_loop()` 创建 `httpx.AsyncClient` 时把配置代理原样传入（`proxy=str(http.get("proxy")) or None`），请求时由 `self.egress.request(...)`（`authorize`）做一次 DNS 校验，但实际 socket 由 httpx 内部自行再次解析 hostname；代理 URL 也没有像 `build_safe_opener` 那样先 `policy.require(proxy)`。
- 问题: `authorize` 校验的 IP 与 httpx 实际连接的 IP 之间存在 TOCTOU（DNS 重绑定可把内网 IP 换入）；代理本身未过策略校验。与 `http_client.py` 的 `PinnedHTTPConnection`（连接字面 IP）形成安全不对称，SSRF 防护被旁路。
- 建议: 给 AsyncClient 挂 `httpx.AsyncHTTPTransport` 的 `local_address`/自定义 transport 固定到 `approved_addresses` 返回的字面 IP；对代理先 `self.target_policy.require(proxy)` 再使用。

### [critical] fetching/browser_fetcher.py:747-748 - 先 `response.body()` 全量下载再检查大小，可能内存耗尽

- 现状: `_capture_response` 中对每个 XHR/fetch 响应调用 `body = response.body()`，之后才判断 `len(body) <= per_response` 与累计上限。
- 问题: Playwright 的 `body()` 会一次性把整个响应拉进内存。恶意/超大的 JSON API 响应（如数 GB）会让 worker 线程内存耗尽并阻塞页面事件循环；且超过上限时 `egress.record_response` 不会被调用，流量预算形同虚设。
- 建议: 先读 `response.headers` 的 `content-length` 拒绝过大响应，或用 `response.body()` 之前按 `max_api_response_bytes+1` 做流式读取截断；对超限响应同样调用 `record_response` 计入预算。

### [high] fetching/async_fetcher.py:40-41, 67-69 - 单实例共享事件循环 + `set_event_loop`，多线程并发调用会崩溃

- 现状: `_ensure_loop` 创建持久 loop 并 `asyncio.set_event_loop(self._loop)`；`fetch()` 直接 `loop.run_until_complete(...)`。
- 问题: 类没有线程安全保证，Pipeline 多线程并发 `fetch` 时，两个线程对同一 loop 调用 `run_until_complete` 会抛 "Event loop is already running" 或行为未定义；`set_event_loop` 还会污染进程全局 loop 状态。`close()`（line 62）跨线程 `run_until_complete(aclose())` 同样有重入风险。
- 建议: 要么给实例加锁并文档化单线程使用，要么放弃持久 loop，改用 `asyncio.run`/每调用独立 loop，或在 `async` 上下文内直接使用。

### [high] fetching/browser_fetcher.py:642-646, 659-661 - 上下文键含配置代理，但建上下文时只取 request.meta 代理，配置代理被静默忽略

- 现状: `_context_key` 使用 `request.meta.get("proxy") or config.http.proxy` 作为缓存键的一部分；`_new_context` 只读 `request.meta.get("proxy")`。
- 问题: 代理通常配置在 `http.proxy`（配置层），此时上下文键包含代理、实际 context 却没有代理——配置代理对 Playwright 完全失效；同时按"带代理的键"复用无代理 context，会话隔离也被破坏。
- 建议: `_new_context` 与 `_context_key` 使用同一代理取值逻辑（meta 优先、回退 config）。

### [high] fetching/browser_fetcher.py:443-450 - 默认配置下 Selenium 引擎永远不可用

- 现状: `_install_selenium_guard` 在 `experimental_selenium_bidi_guard=False` 且 `allow_unintercepted_selenium=False`（均为默认）时直接 `raise RuntimeError`。
- 问题: `browser.engine: selenium` 是受支持配置，但默认配置 100% 抛错，用户必须知道去翻两个隐藏开关；且错误信息在 fetch 层才出现，UX 极差。
- 建议: 引擎选择时即给出前置检查与一次性配置引导（如"首次使用 Selenium 需设置 X 并确认安全含义"），或在文档/校验阶段拒绝并提示。

### [high] fetching/routing.py:24 - SPA 根节点正则要求根元素为空，渲染型页面永远不会被判定为"需要浏览器"

- 现状: `app_roots` 用 `r'<(?:div|main)\b[^>]+id=["\'](?:app|root|__next|__nuxt)["\'][^>]*>\s*</'` 匹配，要求 `>` 后紧跟 `</`（即空根）。
- 问题: 真实 SPA 的 `<div id="root">` 内部有内容，永不匹配；加上 `len(visible) < 80 and scripts >= 3 and app_roots` 三者同时成立，JS 渲染页几乎不会被升级到浏览器抓取，后续得到空/错误正文。
- 建议: 改为 `id` 属性存在即可（如 `<div id="app">` 不带闭合匹配），并把 `app_roots` 改为独立判定而非与空闭合绑定。

### [high] fetching/stealth_enhanced.py:366 - sec-ch-ua 中 Chromium 与 Chrome 版本号两次独立随机，指纹自相矛盾

- 现状: `f'"Chromium";v="{self._rng.randint(125, 131)}"... "Google Chrome";v="{self._rng.randint(125, 131)}"'`，两次 randint 独立。
- 问题: 会产生 `Chromium;v="125", Google Chrome;v="131"` 这种真实 Chrome 不可能出现的组合，反检测指纹反而更易被识别（与模块目的背道而驰）。
- 建议: 先取一个随机版本号，两处共用同一变量。

### [high] extraction/extractors.py:163, 227-231 - 用户正则无防护 + `match.group(group)` 越界

- 现状: `_apply_xpath_rule` 与 `_apply_rule` 中对配置的 `regex` 直接 `re.search(..., flags=re.S)`；`group = rule.get("group", ...)` 后 `match.group(group)`。
- 问题: 病态正则（如 `(a+)+$`）对长文本可触发灾难性回溯导致进程假死；group 名/号不存在时抛未捕获 `IndexError`。这些规则可能来自 `api_discovery`/`intelligent_scraper` 生成的模板或用户导入，输入不可信。
- 建议: 限制正则长度并包一层超时（或预编译拒绝常见嵌套量词）；group 用 `match.groupdict().get(...)` 或 try/except 回退。

### [high] extraction/field_designer.py:55, 199 - 全树 `itertext()`/`iterdescendants()` 每节点重复遍历，O(n²) 且无节点上限

- 现状: `analyze_html` 对 DOM 中每个元素调用 `" ".join(element.itertext())`（line 55）与 `len(list(element.iterdescendants()))`（line 199），且遍历整个文档（只有结果 200 上限，没有遍历节点上限）。
- 问题: 大页面（数 MB HTML）复杂度接近 O(n²)，分析函数会卡死数分钟。
- 建议: 限制最大遍历节点数；把 `itertext`/`iterdescendants` 提前到遍历前一次性计算并缓存到节点，或在评分时改用已收集的文本。

### [high] fetching/browser_fetcher.py:524-526 - fetch 超时后任务仍在后台渲染，无取消机制

- 现状: `fetch()` 用 `task.done.wait(timeout)`，超时直接 `raise TimeoutError`，但任务仍在 worker 队列/渲染中，结果被丢弃。
- 问题: 调用方重试会让 worker 不断堆积卡死任务（`page.goto` 最长 25s/次、可重试一次），浏览器进程与上下文持续被占用，池退化甚至耗尽；超时任务产生的新 context 泄漏。
- 建议: 超时后向 worker 传递取消信号（关闭该 page/context），或缩短渲染整体期限并返回可辨识错误。

### [high] fetching/browser_fetcher.py:454-466 - Selenium BiDi guard 中非 PermissionError 异常既不放行也不拦截，请求挂死

- 现状: `guard()` 只 `except PermissionError: request.fail()`，`else: request.continue_request()`。
- 问题: `authorize` 抛非 PermissionError（如配置错误、意外 RuntimeError）时，既不 fail 也不 continue，被拦截请求永远挂起，直到 driver 超时。
- 建议: 增加兜底 `except Exception: request.fail()`（fail-closed）。

### [medium] fetching/async_fetcher.py:79-82 - 单个请求与批量请求使用两个互不相通的限速器

- 现状: `len(requests) == 1` 走 `self.limiter`（同步 HostRateLimiter，经 `to_thread`），否则走 `self.async_limiter`。
- 问题: 同一主机在两套独立计数器间轮换（一次单请求 + 一次批量），速率限制状态断裂，可形成突发；语义上也难理解。
- 建议: 统一使用 `async_limiter`（`fetch` 走 `_fetch_one`→`fetch_many` 即可），删除分支。

### [medium] fetching/async_fetcher.py:116 - 3xx 无 Location 头时被当作成功响应返回

- 现状: 重定向判断 `status in {301,302,303,307,308} and response.headers.get("location")`，缺 Location 时直接落入"成功"分支。
- 问题: 返回 3xx 状态 + 重定向页正文的 `FetchResult`，调用方误以为成功。
- 建议: 对 3xx 无 Location 的情况 `raise PermanentFetchError`。

### [medium] fetching/async_fetcher.py:146-147 - 永久性错误不再记录到 egress，熔断器永不打开

- 现状: `except (ResponseTooLargeError, PermanentFetchError): raise` 前未 `egress.record_failure`。
- 问题: 持续 4xx/超限不会累加熔断计数，与 TransportError 分支（line 166 记录）不对称。
- 建议: 永久性错误也 `record_failure(..., retryable=False)` 或明确设计说明。

### [medium] fetching/action_recorder.py:177-195 - `page.goto` 失败时函数整体异常退出，输出文件不落盘、context/browser 未显式关闭

- 现状: goto 不在 try 内，出错直接抛出，`sequence.save`、截图、`context.close()` 全部跳过（仅靠 `with sync_playwright()` 兜底关闭驱动）。
- 问题: 用户只得到原始 Playwright 异常，录制文件丢失；浏览器进程可能残留。
- 建议: 用 try/finally 保证 `context/browser.close()` 与 `sequence.save`；goto 失败给出友好中文提示。

### [medium] fetching/action_recorder.py:45-47 - fill 合并只比对 action+selector，忽略 secret 标记差异

- 现状: 新 fill 事件会无条件替换同 selector 的上一条 fill。
- 问题: 若前一条是明文、后一条是密码（secret），合并后值变为占位符或反之，录制的动作序列与真实输入不一致。
- 建议: 合并时同时比较 `secret` 语义或直接不合并密码类动作。

### [medium] fetching/action_recorder.py:53 - `int(event.get("value", 1000))` 对非数字 wait 值抛 ValueError

- 现状: 页面 JS 若发 `{"type":"wait","value":"abc"}` 直接 `int()` 崩溃，录制中断。
- 建议: try/except 回退默认值并过滤。

### [medium] fetching/archives.py:47-71 - 对 zip 特殊文件类型（FIFO/字符设备/块设备）不拒绝，仅拒符号链接

- 现状: `_zip_members` 只判断 `is_link`；unix_mode 为 FIFO/设备时照常提取。
- 问题: 在 POSIX 上若未来改用可保留权限的提取路径会创建设备节点；当前虽用 `_copy_limited` 写普通文件而安全，但校验层语义不完整。
- 建议: 在 `_validate_members` 中统一拒绝 `not (is_dir or is_file)` 的成员。

### [medium] fetching/captcha_ocr.py:39-44 - `_ensure_ocr` 无锁，并发首次调用可能重复初始化 ddddocr

- 现状: `if self._ocr is None: self._ocr = ddddocr.DdddOcr(...)`。
- 问题: 全局单例有锁，但 `recognize` 并发首次进入时两个线程都初始化，浪费数百 MB 内存与启动时间（ddddocr 模型加载较重）。
- 建议: 在 `_ensure_ocr` 内加 `threading.Lock` 双检。

### [medium] fetching/http_client.py:341-345 - 压缩流不完整时抛裸 `ValueError`，且 try/finally+del 无意义

- 现状: `if not decompressor.eof: raise ValueError("压缩响应不完整")`（外层 try/finally 只 `del decompressor`）。
- 问题: 该 ValueError 不被 `fetch` 的 except 分支捕获，用户看到莫名错误；`finally: del` 是死代码。
- 建议: 包装为 `ResponseTooLargeError`/`TransientFetchError` 或带上下文的异常，去掉无意义 finally。

### [medium] fetching/http_client.py:195, 291-316 - `_ensure_login` 非线程安全，多线程并发会重复登录

- 现状: `_login_done` 无锁读写。
- 问题: 两个线程同时 `fetch` 时都执行登录 POST，cookie 重复写入、登录可能竞态失败。
- 建议: 加锁或把登录移到初始化/首次独占路径。

### [medium] fetching/http_client.py:22, 197-199 - 配置键不一致：`retries` vs `retry_max`，`parse_retry_config` 的 `max_retries`/`status_codes` 无人使用

- 现状: 抓取器读 `http.retries`，`parse_retry_config` 读 `http.retry_max`；其返回的 `max_retries`、`status_codes` 字段在 http_client 中从未被消费（只用 base/max/jitter）。
- 问题: 两个配置旋钮并存且其中一个不生效；用户改 `retry_max` 不会影响实际重试次数。
- 建议: 统一键名，删除死字段或让调用方实际使用。

### [medium] fetching/http_client.py:276-279, 288-289 - 最终失败抛出裸 URLError/底层异常，未统一为项目异常模型

- 现状: TransportError 类重试耗尽后 `raise` 原始 `urllib.error.URLError`。
- 问题: 上层 `describe_error` 只能归为 generic；错误码与 `TransientFetchError` 体系不一致，影响重试判定与诊断。
- 建议: 抛 `TransientFetchError(...) from exc`。

### [medium] fetching/http_client.py:319-322 - 复合 Content-Encoding（如 "gzip, br"）不处理，压缩体原样返回

- 现状: 仅当 `encoding in {"gzip","deflate"}` 才解压。
- 问题: 服务器返回多级编码时结果仍是压缩字节，写入输出文件即损坏。
- 建议: 按 `split(",")` 逆序逐层解压，或对无法处理的编码显式报错。

### [medium] fetching/http_client.py:24-52 - HTTPError 响应体未读取即放弃，连接不归还/泄漏

- 现状: 4xx/5xx 时 `opener.open` 直接抛 HTTPError，其文件对象从不 read/close。
- 问题: urllib 的 HTTPError 持有连接，不读 body 就不归还连接池（虽最终随 GC 释放，但高并发下 fd/连接堆积）。
- 建议: 在 `except HTTPError` 中 `exc.close()` 或 `exc.read()`。

### [medium] fetching/retry.py:40-46 - `parse_retry_config` 默认 `retry_on_status=[429,502,503,504]` 与 `RETRYABLE_STATUS` 集合不一致

- 现状: 两处维护两套可重试状态集合（retry.py:8 的 frozenset 含 408/425/500，此处默认列表不含）。
- 问题: 规则漂移，调用方若按 status_codes 判断会得到与 RETRYABLE_STATUS 不同的结果。
- 建议: 复用同一常量。

### [medium] fetching/routing.py:21 - script/style 剥除正则在大前缀上可能二次回溯

- 现状: `re.sub(r"<script\b[^>]*>.*?</script>|<style...", ...)`，输入限 200KB 但无执行时间上限。
- 问题: 对缺少闭合 `</script>` 的对抗性页面，惰性量词可导致 O(n²) 级扫描，单页拖慢整体。
- 建议: 用字符串分界/一次性扫描替代正则，或加超时保护。

### [medium] fetching/session.py:22-31 - `save()` 失败（只读目录）会连带整个 fetch 失败

- 现状: `self.jar.save(...)` 异常未捕获。
- 问题: 网络响应已成功，仅因 cookie 落盘失败导致抓取整体报错。
- 建议: 捕获 OSError 记 warning，不中断结果返回。

### [medium] fetching/session.py:44-46 - `setdefault(path, CookieSession(path))` 每次调用都构造新实例并重读文件

- 现状: 参数被急切求值，即使 key 已缓存也执行构造（含磁盘读取）。
- 问题: 高频 fetch 时冗余 I/O。
- 建议: 先 `if path in _SESSIONS` 再构造。

### [medium] fetching/stealth_enhanced.py:517-521 - 时区伪装只覆盖 getTimezoneOffset 且映射表缺项，洛杉矶/芝加哥回落到上海偏移

- 现状: `_offsets` 含 Asia/Shanghai/Tokyo 等，但 `_TIMEZONES` 里的 `America/Los_Angeles`、`America/Chicago` 缺项，回退 -480。
- 问题: "洛杉矶"指纹返回上海时区偏移，自相矛盾更易识别；原型级重写还影响全页所有 Date。
- 建议: 补齐映射（LA=300/240、Chicago=360/300 等），或在缺失时排除该时区选项。

### [medium] fetching/stealth_enhanced.py:441-450 - Canvas 噪声对全像素做同一 XOR 且作用于 alpha，还可能在 tainted canvas 上抛异常

- 现状: `imageData.data[i] ^= fp.canvas_noise % 256`（每个字节同一常数），并在 `toDataURL` 前 `getImageData`。
- 问题: ① tainted canvas（跨域图）调用 `getImageData` 会抛 SecurityError，破坏页面截图/导出功能；② 常数 XOR 极弱且改到 alpha 通道产生可见伪影。
- 建议: 仅当画布未 tainted 时注入（try/catch），噪声改为逐像素伪随机且避开 alpha。

### [medium] fetching/stealth_enhanced.py:394-409 - `apply_to_playwright_context` 未被 BrowserFetcher 集成，属文档声称之外的死特性

- 现状: 模块 docstring 声称"自动集成到 BrowserFetcher"，但 `_new_context` 只注入 stealth.min.js + webdriver 覆盖，从不调用本方法；line 399 还可能在空 context 上 `new_page()` 制造永不关闭的页面。
- 问题: 特性不可达，且未使用路径存在资源泄漏风险。
- 建议: 在 `_new_context` 中按 StealthLevel 接入，或移除误导性文档。

### [medium] fetching/stealth_enhanced.py:232-245 - `validate_proxy` 默认访问 httpbin.org 泄露流量，且 read 无上限

- 现状: `test_url="http://httpbin.org/ip"`，`_ = resp.read()` 无大小限制，且未走 egress 策略。
- 问题: 代理校验把出口流量发往第三方；恶意代理可返回海量数据耗尽内存。
- 建议: read 加限制、允许配置测试目标，并纳入 egress 校验。

### [medium] fetching/streams.py:44-53 - SSE 每行 `readline()` 无独立读超时，静默连接可阻塞至 socket 超时且无反馈

- 现状: `duration_seconds` 只在两次 readline 之间检查；`readline()` 阻塞期完全靠 opener 的 socket 超时兜底。
- 问题: 服务器发一条消息后静默，函数会被卡住最多 `timeout_seconds`(25s) 才抛裸 `socket.timeout`，与"duration_seconds 决定时长"的语义不符，失败原因不清晰。
- 建议: 用非阻塞/select 实现逐行读超时，超时后按"流结束"处理并记录诊断。

### [medium] fetching/streams.py:91-93 - `except TimeoutError` 在 Python 3.10 及以下捕不到 `asyncio.TimeoutError`

- 现状: `asyncio.wait_for` 抛 `asyncio.TimeoutError`；3.10 以前它不等于内置 `TimeoutError`。
- 问题: 项目要求 Python 3.10+（使用 `str|None`/match），在 3.10 上超时异常未捕获直接上抛。
- 建议: `except (asyncio.TimeoutError, TimeoutError)` 或直接用 3.11 别名。

### [medium] fetching/streams.py:109-115 - `asyncio.run()` 在已运行事件循环内调用会抛 RuntimeError

- 现状: `collect_websocket` 同步包装直接 `asyncio.run(...)`。
- 问题: 若被 async 上下文（如异步抓取流程/插件）调用即崩溃；每次调用也自建新 loop，不能与其它 asyncio 协作。
- 建议: 提供 async 版本并在同步包装中用 loop 检测，或文档化"仅限同步线程调用"。

### [medium] fetching/streams.py:94-96 - WebSocket 消息计数先累加再判断，超限后仍 append（轻微）且二进制/文本混算

- 现状: `consumed += ...` 后 `if consumed > maximum_bytes: raise`，超限消息本身不会入 records（raise 在 append 前），无误；但二进制与文本按不同口径计字节，账户与真实流量存在偏差。
- 问题: 计数口径不一致（次要）。
- 建议: 统一按原始字节长度计算。

### [medium] extraction/__init__.py:3 - 包内使用绝对导入，命名不一致且破坏重定位

- 现状: `from omnicrawl.extraction.ai_graph import AIGraphExtractor`。
- 问题: 全项目其它模块用相对导入；包被改名/内嵌时此处必然断链；且仅导出 AIGraphExtractor，其余提取器未导出。
- 建议: 改为 `from .ai_graph import AIGraphExtractor` 并考虑统一导出。

### [medium] extraction/ai_graph.py:310 - `data.get("choices", [{}])[0]` 在 `choices: []` 时抛 IndexError

- 现状: 仅当 key 缺失才用默认 `[{}]`。
- 问题: 服务返回空 choices 数组时 `[][0]` 未捕获崩溃。
- 建议: `(data.get("choices") or [{}])[0]`。

### [medium] extraction/ai_graph.py:337-347 - JSON 解析失败被伪装成"成功空块"，与 D55 意图相悖

- 现状: `_parse_response` 失败返回 `{"fields": {}, "confidence": 0.0}`；`extract` 将其计入 ok_results。
- 问题: 一个分块解析失败不进入 `failed_chunks`，全部分块失败才显式报错的保证被削弱；AI 输出损坏被静默吞掉。
- 建议: 解析失败抛出/标记为失败块（由 `run_one` 捕获计入 errors）。

### [medium] extraction/ai_graph.py:364-368 - 合并时 `if not value` 丢弃 0/False/空串等合法值

- 现状: 假值字段一律跳过合并。
- 问题: 价格 0、布尔 False 等合法数据被丢弃，且后续"字段冲突"检测基于假值跳过，逻辑被短路。
- 建议: 改为 `if value is None: continue`，并用 `is None` 判断是否已合并。

### [medium] extraction/ai_graph.py:184-193 - 固定分块会在任意字符处切断 HTML 标签，LLM 收到碎片

- 现状: `_fixed_chunk_split` 按字符切片。
- 问题: 属性/标签被拦腰截断，提取质量下降；heading 模式则对 `>` 出现在引号属性中的 HTML 失效。
- 建议: 用标签感知的边界切块或至少在标签/注释边界对齐。

### [medium] extraction/api_discovery.py:126-135 - `_walk` 无深度限制，深层嵌套 JSON 触发 RecursionError

- 现状: `_walk` 递归无深度上限（`infer_schema` 有 6 层上限）。
- 问题: 服务器返回 ~1000 层嵌套即可让分析崩溃（DoS 面）。
- 建议: 给 `_walk` 加深度上限并跳过超深分支。

### [medium] extraction/api_discovery.py:216, 224 - `write_discovery_bundle` 把外部 `name` 直接拼入路径，存在路径遍历

- 现状: `f"work/{name}_{index}"` 与 `output_dir / f"{name}_{index}.yaml"`。
- 问题: 调用方若传 `name="../x"` 会写出输出目录，公开 API 未做消毒。
- 建议: 对 name 做 `re.sub(r"[^\w\-]", "_", ...)` 或仅取 `Path(name).name`。

### [medium] extraction/extractors.py:337-339 - JSON 解析失败抛裸 JSONDecodeError，无 URL/上下文

- 现状: `JSONProcessor.process` 直接 `json.loads(decode_body(result))`。
- 问题: 服务器对 .json URL 返回 HTML 时错误难诊断。
- 建议: 包装为带 `result.final_url` 的异常。

### [medium] extraction/extractors.py:146-147 - XPath 语法错误时抛未捕获 lxml 异常

- 现状: `root.xpath(xpath)` 无 try。
- 问题: 用户配置错误产生 XPathEvalError 原始异常。
- 建议: 捕获并包装为 `SelectorSyntaxError`（项目已有该错误类）。

### [medium] extraction/extractors.py:435-439, 419-432 - `TableProcessor` 已注册但 `choose_processor` 永不返回 "table"，死特性

- 现状: `register` 注册 table，`choose_processor` 返回集合为 {json, html, text, binary}。
- 问题: 标准管线永远到不了 TableProcessor，用户设 `extract.mode=table` 也走不到。
- 建议: 在 choose_processor 支持 table，或删除注册避免误导。

### [medium] extraction/extractors.py:199-215 - candidates 列表分支 `value != []` 与默认值比较语义含糊

- 现状: `if value is not None and value != "" and value != []:` 决定是否命中。
- 问题: 结构化提取若返回空列表会被误判为"未命中"继续尝试候选；且 `default` 恰好为 `[]` 时行为异常。
- 建议: 用 `value not in (None, "", [])` 统一判定并明确默认值语义。

### [medium] extraction/extractors.py:12-22 - decode_body 用 latin-1 兜底，任何字节都能"成功"解码，易产生乱码而非继续降级

- 现状: 候选序列 header 字符集 → utf-8 → gb18030 → latin-1。
- 问题: GBK 页面若 header 错误标成 latin-1，直接得到乱码文本（latin-1 永不失败）。
- 建议: 去掉 latin-1 兜底或用检测（如 html5lib/chardet）后尝试。

### [medium] extraction/html_tools.py:95-108 - `_mini_select` 将子代选择器 `>` 与后代选择器同等处理，且多属性选择器解析错误

- 现状: token 切分后 `>` 被丢弃，`div > span` 与 `div span` 行为相同；`_match_simple` 只匹配第一个 `[attr]`，`input[type=text][name=x]` 后半被静默忽略。
- 问题: 无 bs4 的回退路径下选择器语义错误，可能选中错误节点。
- 建议: 追踪直接子关系，循环处理所有属性对。

### [medium] extraction/html_tools.py:30-41 - `get_text` 二次 HTML 反转义可能损坏内容

- 现状: 数据经 `HTMLParser(convert_charrefs=True)` 已解码，`get_text` 又 `html.unescape`。
- 问题: 原文 `&amp;amp;` 经两次解码变成 `&`，文本被破坏。
- 建议: 去掉重复 unescape 或只在原始 parts 上做一次。

### [medium] extraction/intelligent_scraper.py:64-104 - `_walk` 每节点调用 `text_content()`，子树文本重复计算 O(n²)，深 DOM 还可能递归溢出

- 现状: 每个元素 `element.text_content()`（O(子树)），深度无上限。
- 问题: 5000 节点上限前，深/大页面解析耗时显著；超深 DOM 触发 RecursionError。
- 建议: 用一次性遍历收集文本，给深度加上限。

### [medium] extraction/intelligent_scraper.py:513-516 - 生成的"下一页"配置把 XPath 塞进 `selector` 字段

- 现状: `{"action": "click", "selector": pag.get("xpath", "")}`。
- 问题: `BrowserAction.selector` 语义是 CSS；Playwright `locator()` 对 `//` 前缀自动识别 XPath 勉强可用，Selenium 的 CSS_SELECTOR 查找会失败。
- 建议: 增加 xpath 字段或在生成配置时转换为 CSS。

### [medium] extraction/topic_filter.py:44-50 - `filter_records` 原地改写输入记录，注入 `_topic_match` 污染数据

- 现状: `record["_topic_match"] = {...}`。
- 问题: 调用方共享的 records 被副作用修改；若同一记录后续导出/传给其它阶段，附带的内部字段会外泄。
- 建议: 返回 (filtered, decisions) 或只复制追加字段到新 dict。

### [medium] extraction/topic_filter.py:62-75 - `_text` 忽略 list/dict 字段值，`match_on` 指定的数组字段不参与匹配

- 现状: 仅收集 str/int/float/bool 标量。
- 问题: `tags: ["ai","ml"]` 等列表字段即使显式列入 match_on 也不匹配。
- 建议: 对 list/dict 展开递归收集字符串。

### [low] fetching/action_recorder.py:24 - `config()` 过滤集合 `{"", 0}` 对字符串 "0" 不过滤，wait_ms=0 的配置仍被保留

- 现状: `if value not in {"", 0}`，`"0"`（字符串）不在集合内。
- 问题: 少量不一致，影响可忽略。
- 建议: 统一类型判定。

### [low] fetching/action_recorder.py:168-169 - 录音浏览器不带配置代理/UA，且 `page.goto` 用默认超时

- 现状: `chromium.launch(headless=False)` + `new_context()` 无 options。
- 问题: 录制环境与真实抓取环境指纹不一致；goto 依赖 Playwright 默认 30s。
- 建议: 显式传入 timeout 与 UA。

### [low] fetching/action_recorder.py:184-189 - 截图失败用 `Path()` 占位，语义晦涩

- 现状: `screenshot = Path()`（值为 "."）。
- 问题: 后续靠 `screenshot.is_file()` 判断，可读性差。
- 建议: 用 `None` 表达"无截图"。

### [low] fetching/action_recorder.py:190-194 - `context.close()` 抛异常时 `browser.close()` 被跳过

- 现状: 两者在同一 try 内。
- 问题: context 关闭失败时浏览器进程延迟回收。
- 建议: 拆分独立 try。

### [low] fetching/action_recorder.py:201 - `timed_out` 用 goto 之后的 started 计时，不含加载时间

- 现状: `started` 在 `page.goto` 成功后记录。
- 问题: 统计口径偏差。
- 建议: 提前记录 started。

### [ux] fetching/action_recorder.py:163-166 - 录制模式默认拒绝 localhost（allow_private_network=False），本地开发站点无法录制且无提示

- 现状: `policy.require(url)` 直接抛 PolicyBlockedError。
- 问题: 用户录制本地页面第一时间失败，错误信息未说明可开 `allow_private_network`。
- 建议: 在异常信息中附带解决方案。

### [low] fetching/archives.py:68 - 压缩比检查对 tar 恒为 1.0（compressed_size==size），形同虚设

- 现状: `_tar_members` 把 `compressed_size` 设为 `size`。
- 问题: tar 分支的 zip-bomb 防护实际失效（tar 无法先验压缩比，属可接受设计但应注明）。
- 建议: 对 tar 在解压流式阶段做"解压字节 > 声明"防护（`_copy_limited` 已有）。

### [low] fetching/archives.py:83-84 - 解压后才做 `written != expected_size` 校验，不匹配时已占用空间

- 现状: `_copy_limited` 结束时校验。
- 问题: 声明撒谎的成员先写满磁盘再报错；staging 随后被清理，影响有限。
- 建议: 接受现状或提前以声明大小预校验。

### [low] fetching/archives.py:90-91, 149-150 - `unix_mode`/`isinstance(opener, ZipInfo)` 分支冗余

- 现状: `is_link` 判定与 `isinstance` 检查均有恒定结果路径。
- 问题: 死代码/冗余防御。
- 建议: 保留防御但可简化。

### [low] fetching/archives.py:134 - 目标目录非空检查与后续 `rmdir` 之间存在 TOCTOU

- 现状: line 134 检查后，line 174-175 才 rmdir。
- 问题: 并发写入会让 rmdir 抛 OSError（已由 except 清理 staging，可接受）。
- 建议: 知晓并文档化。

### [low] fetching/archives.py:137 - `mkdtemp` 在 try 外创建，若创建后异常仍能进入清理分支（无问题，仅结构说明）

- 现状: staging 创建于 try 之外。
- 问题: 若 `mkdtemp` 自身抛错则无清理需求；结构上无 bug。
- 建议: 无。

### [low] fetching/async_fetcher.py:100 - `retries=max(1, ...)` 使配置 `retries: 0` 无法关闭重试

- 现状: 强制至少 1 次。
- 问题: 与配置语义冲突（用户想不重试做不到）。
- 建议: 允许 0 或用显式开关。

### [low] fetching/async_fetcher.py:125-127 - `raise_for_status()` 双重调用冗余

- 现状: RETRYABLE 分支先 raise，随后无条件再 raise。
- 问题: 第一处对可重试码必抛，第二处不可达其"可重试"意图，属冗余但无害。
- 建议: 清理。

### [low] fetching/async_fetcher.py:43 - `Limits(max_connections=concurrency)` 对 0/负数并发配置抛 ValueError

- 现状: 直接从配置取 int。
- 问题: 配置校验缺位时崩溃点不友好。
- 建议: `max(1, ...)`。

### [low] fetching/async_fetcher.py:175 - `assert last is not None` 依赖重试循环必有异常

- 现状: for 循环后 assert。
- 问题: 逻辑上成立，但 assert 在 -O 下被剔除后有潜在 NameError 路径（不可达）。
- 建议: 用显式 raise。

### [ux] fetching/async_fetcher.py:167 - 最终传输错误抛出原始 httpx 异常，缺少可操作信息

- 现状: `raise` 原异常。
- 问题: 用户看不到重试次数/超时配置建议。
- 建议: 包装 TransientFetchError 并附 retry 信息。

### [low] fetching/http_client.py:163-164 - `verify_tls=false` 时用 `ssl._create_unverified_context()` 完全关闭证书校验

- 现状: 配置驱动关闭 TLS 校验。
- 问题: 可被 MITM；且使用私有 API。
- 建议: 用 `create_default_context(); context.check_hostname=False; context.verify_mode=CERT_NONE` 或至少明确警告日志。

### [low] fetching/http_client.py:221-227 - 登录响应体读取上限 1MB 后未关闭/未解压处理

- 现状: `response.read(1024*1024)`。
- 问题: 登录响应若超 1MB 被截断但视为成功；无 content-encoding 处理（urllib 会自动解压 gzip）。
- 建议: 关注即可。

### [low] fetching/http_client.py:313-314 - 登录失败分支 `status >= 400` 基本不可达

- 现状: 4xx/5xx 由 opener.open 抛 HTTPError 先到。
- 问题: 死分支 + RuntimeError 信息过泛。
- 建议: 删除或改为基于响应体校验。

### [low] fetching/http_client.py:349-356 - `urlencode` 对非 dict payload 抛 TypeError

- 现状: `encode_request_payload` 假定 payload 可被 urlencode/json。
- 问题: 配置给 list 时异常不友好。
- 建议: 显式校验并给出中文错误。

### [low] fetching/retry.py:12 - `Retry-After` 头只查两种大小写写法

- 现状: 仅 `Retry-After`/`retry-after`。
- 问题: 其它大小写组合（少见）漏判。
- 建议: 遍历 key casefold 匹配。

### [low] fetching/retry.py:36 - `maximum` 为负时退避时间可为负导致 sleep 抛错

- 现状: `min(maximum, ...)` 未钳制负数。
- 问题: 配置异常时才触发。
- 建议: `max(0, min(...))`。

### [low] fetching/routing.py:18 - 标记 "captcha" 触发大量误升级

- 现状: 正文前 200KB 含 "captcha" 即升级浏览器。
- 问题: 文章提到验证码也会走浏览器，浪费资源。
- 建议: 收紧为 `cf-chl`/Cloudflare 等特征或组合判断。

### [low] fetching/session.py:29 - Windows 上 `os.chmod(0o600)` 仅设只读位，cookie 文件可被本机其它用户读取

- 现状: chmod 在 win32 基本无效。
- 问题: 会话 cookie（可能含登录态）权限保护在 Windows 失效。
- 建议: 用 NTFS ACL 或至少加密存储。

### [low] fetching/stealth_enhanced.py:330 - `seed or int(time.time()*1000)` 使 seed=0 无法复现

- 现状: 0 被当作 falsy。
- 问题: 显式 seed=0 意图被破坏。
- 建议: `seed if seed is not None else ...`。

### [low] fetching/stealth_enhanced.py:349-352 - `try/except` 包 `random.choice` 为无用死代码

- 现状: `_LANGUAGES` 恒定，choice 不会失败。
- 问题: 冗余。
- 建议: 删除。

### [low] fetching/stealth_enhanced.py:192-199 - 代理全失败后直接清零所有失败计数，可能立刻重选已知坏代理

- 现状: `self._failures[p] = 0` 全量重置。
- 问题: 状态丢失。
- 建议: 重置仅对本次选择生效或冷却。

### [low] fetching/stealth_enhanced.py:458-464 - WebGL 伪装只 patch `WebGLRenderingContext`，WebGL2 页面读到真实值

- 现状: 未覆盖 `WebGL2RenderingContext.prototype`。
- 问题: 指纹不一致。
- 建议: 同时 patch WebGL2。

### [low] fetching/stealth_enhanced.py:528-537 - Selenium UA 伪造未同步 Client Hints，sec-ch-ua 与 UA 不一致

- 现状: 仅设 user-agent。
- 问题: 现代浏览器通过 Client Hints 仍暴露真实信息。
- 建议: 增加 `--sec-ch-ua` 参数同步。

### [ux] fetching/streams.py:23-24 - SSE/WebSocket 默认仅收 100 条消息（max_messages=100），长流被静默截断

- 现状: `int(source.get("max_messages", 100))`。
- 问题: 无任何提示地停止，用户以为流结束。
- 建议: 结束时在结果/日志注明"达到 max_messages"。

### [low] fetching/streams.py:48-53 - SSE 流在无空行 EOF 时最后一个事件被丢弃

- 现状: 仅空行触发 record。
- 问题: 规范上事件以空行结束，但部分服务器不加。
- 建议: EOF 时 flush 残留 event。

### [ux] extraction/ai_graph.py:280 - api_key 为空时发 `Authorization: Bearer ` 直到 401 才发现

- 现状: 无前置校验。
- 问题: 白耗一次调用、错误延迟。
- 建议: `_extract_chunk` 开头校验 key 并提示。

### [low] extraction/ai_graph.py:236-239 - 429/5xx 重试固定指数退避，未尊重 Retry-After，未加抖动

- 现状: `await asyncio.sleep(1.0 * (2 ** attempt))`。
- 问题: 与 `retry.py` 的成熟退避策略不一致。
- 建议: 复用 retry 模块。

### [low] extraction/ai_graph.py:246 - 2xx 但响应体非 JSON 时 `resp.json()` 抛未包装异常

- 现状: 直接 `return await resp.json()`。
- 问题: ContentTypeError 未捕获，错误信息含服务端内容有限。
- 建议: 捕获并包装。

### [low] extraction/ai_graph.py:334 - 未闭合 ``` 代码块时切片逻辑可能截掉部分 JSON

- 现状: `lines[1:-1] if lines[-1].strip()=="```" else lines[1:]`。
- 问题: 末尾标记缺失时保留整段（含 ```），后续正则兜底；边界行为略怪。
- 建议: 按首个 ``` 与末尾 ``` 双端定位。

### [low] extraction/ai_graph.py:375 - 置信度只在有字段的分块上平均，无字段分块不计入

- 现状: `if fields and isinstance(conf, (int, float))`。
- 问题: 与文档一致，但空块置信度被忽略，统计略偏乐观。
- 建议: 保持并注明。

### [low] extraction/api_discovery.py:78 - 排序键 `item.endpoint` 区分大小写，同类端点可能无法归并

- 现状: normalize_endpoint 保留原始大小写。
- 问题: `/API/User` 与 `/api/user` 视为两个端点。
- 建议: 比较/排序时 casefold。

### [low] extraction/api_discovery.py:116 - `oneOf` 数组序列化可能非常大

- 现状: 对 >1 种 schema 生成完整 oneOf 全量嵌套。
- 问题: 大样本下报告膨胀。
- 建议: 只保留类型名数组。

### [low] extraction/api_discovery.py:290 - `suggested_fields` 为空时 ≥2 样本即判 validated，样本结构差异被忽略

- 现状: `not profile.suggested_fields or common_fields`。
- 问题: 空字段集时过度乐观。
- 建议: 至少要求 `len(item_counts)` 一致或公共字段非空。

### [low] extraction/api_discovery.py:146 - `_best_item_array` 的语义键仅识别 items/results/data/records/rows/list

- 现状: 其它常见键（`list` 已含、`products`/`articles` 等）不算语义。
- 问题: 轻微影响候选排序。
- 建议: 扩展键表。

### [low] extraction/extractors.py:294 - 默认 HTML 提取把 text 截断到 5000 字符，静默丢内容

- 现状: `node_text(item)[:5000]`。
- 问题: 长正文被截断且无标记。
- 建议: 截断时记录长度/加省略标记。

### [low] extraction/extractors.py:411 - 表格重复列名时后列覆盖前列（dict 键冲突）

- 现状: `data[headers[index]] = value`。
- 问题: 数据丢失。
- 建议: 重名列加序号。

### [low] extraction/extractors.py:426 - URL 以 "/" 结尾强制判为 HTML，即使 content-type 为 text/plain/json 之外的接口根

- 现状: `result.final_url.lower().endswith(("/",))`。
- 问题: 纯文本接口被当 HTML 解析。
- 建议: 优先 content-type，再降级 URL。

### [low] extraction/field_designer.py:104-108 - 依赖响应头 charset 解码，头错误则乱码（无检测兜底）

- 现状: `result.body.decode(encoding, errors="replace")`。
- 问题: GBK 页面被 UTF-8 解码成乱码进入分析。
- 建议: 复用 extractors.decode_body 的降级链。

### [low] extraction/field_designer.py:97-99 - `analyze_url` 以 cwd 为 workspace 并落盘 `.field_designer.yaml`/目录

- 现状: `Path.cwd().resolve()` 作为 root。
- 问题: 库函数调用产生工作目录副作用。
- 建议: 使用 tempfile 或调用方指定目录。

### [low] extraction/html_tools.py:52-56 - `_MiniParser` 丢弃纯空白文本节点，内联排版文本丢失空格

- 现状: `handle_data` 仅 append `strip()` 后的非空数据。
- 问题: `Hello <b>x</b>` 中间空格靠 join 兜底，但 `<pre>` 缩进/换行被破坏。
- 建议: 对 pre/格式化场景保留原始空白。

### [low] extraction/html_tools.py:111-116 - bs4 缺省时 `Tag=()` 占位类型，弱可读

- 现状: `Tag = ()`。
- 问题: 类型占位怪异。
- 建议: 用 `None`/`typing.Any`。

### [low] extraction/intelligent_scraper.py:76 - 每个节点 `text_content()` O(子树) 且只取 500 字符

- 现状: `(element.text_content() or "").strip()[:500]`。
- 问题: 计算浪费 + 截断。
- 建议: 一次遍历缓存。

### [low] extraction/intelligent_scraper.py:496 - 无 URL 时静默回填 `https://example.com`

- 现状: `"seeds": [url] if url else ["https://example.com"]`。
- 问题: 生成配置里出现无意义的示例种子，用户易误用。
- 建议: 无 URL 时留空并在 desc 标注。

### [low] extraction/intelligent_scraper.py:549 - CLI 用 utf-8 读 HTML 文件，GBK 中文文件乱码

- 现状: `Path(args.input).read_text(encoding="utf-8", errors="replace")`。
- 问题: Windows 常见 GBK 文件被替换字符污染分析。
- 建议: 尝试 utf-8→gb18030 降级。

### [low] extraction/topic_filter.py:59 - 术语仅 casefold，未归一化空白

- 现状: `str(item).strip().casefold()`。
- 问题: `"AI "` 与 `"AI"`、`"AI 学习"` 中间多空格不匹配。
- 建议: 折叠连续空白。

### [low] extraction/topic_filter.py:71 - bool 值被转成 "True"/"False" 参与主题匹配

- 现状: `isinstance(item, (str,int,float,bool))`。
- 问题: 无意义噪声词。
- 建议: 排除 bool。

### [ux] fetching/browser_fetcher.py:596 - `wait_until="networkidle"` 对长轮询/SSE 页面必然 25s 超时

- 现状: 默认 networkidle，timeout 取 http.timeout_seconds*1000。
- 问题: 持续出流量的页面永远不 idle，用户看到莫名超时。
- 建议: 默认 "load" 或先 domcontentloaded 再降级等待。

### [ux] fetching/browser_fetcher.py:437 - Selenium 结果状态恒为 200，重定向/404 页面无感知

- 现状: 硬编码 200。
- 问题: 用户看到"成功"却拿到错误页正文。
- 建议: 尝试读取 performance/驱动接口获取真实状态。

### [ux] fetching/captcha_ocr.py:60 - 识别失败与"验证码为空"都返回空字符串，调用方无法区分

- 现状: `return str(result) if result else ""`。
- 问题: 空验证码 vs 识别失败混淆。
- 建议: 返回 `(text, success)` 或抛异常。

### [ux] fetching/http_client.py:264 - 非可重试 HTTP 错误只给 `HTTP 4xx: url`，无建议

- 现状: `raise PermanentFetchError(f"HTTP {exc.code}: {request.url}")`。
- 问题: 信息过简。
- 建议: 附带响应体片段/重试建议。

### [ux] fetching/browser_fetcher.py:125 - `wait_for_url` 默认 `**/*` 匹配任意 URL，等于永不生效

- 现状: `str(action.value or "**/*")`。
- 问题: 未配置 pattern 时静默跳过等待，用户以为在等目标 URL。
- 建议: 无 pattern 时明确报错或跳过并记录。

---

## 补充说明

- 全部 20 个文件通过 `python -m py_compile`，无语法错误。
- 跨文件共性问题：
  1. **Python 3.10 兼容性**：`except TimeoutError`（streams.py:92、ai_graph.py:247）在 3.10 捕不到 `asyncio.TimeoutError`；代码使用 `str|None`/`match` 说明支持 3.10。
  2. **错误模型不统一**：多个抓取器最终抛出底层库异常（httpx/urllib/lxml/JSONDecodeError）而非项目 `TransientFetchError`/`SelectorSyntaxError`。
  3. **egress 记账不对称**：永久性错误（async_fetcher.py:146、http_client.py:263）不记 `record_failure`，熔断器不感知 4xx。
  4. **敏感数据外泄风险**：browser_fetcher.py:732-741 的 POST payload、stealth_enhanced.py:232 的 httpbin 探活、session.py 在 Windows 上权限无效。
