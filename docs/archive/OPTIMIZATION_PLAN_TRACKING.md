# 优化覆盖追踪（全覆盖注册表）

> 本文档是 `OPTIMIZATION_PLAN_FULL.md` §6 覆盖矩阵的**逐条扩展**，保证两份审计源文件 100% 逐条可追溯：
>
> - **源 A（终版）** `FINAL_FINDINGS_SUMMARY.md`：P0#1-24、P1#25-110、P2/P3 主题簇、优化 0-18、根因 1-10、5 条假数据。
> - **源 B（问题清单）** `问题清单与优化方案.html`：P0#1-15、P1#16-57、P2#58-125、P3#126-156、方案 1-13。
>
> 两套编号相互独立，均在本表**逐条**映射到阶段/任务。`→ 后项` 表示并入已列任务的补充验收项（同一任务内一并完成）。

## 0. 覆盖统计

| 源 | 应覆盖 | 已覆盖 | 状态 |
|---|---|---|---|
| A P0#1-24 | 24 | 24 | 100% |
| A P1#25-110 | 86 | 86 | 100% |
| B P0#1-15 | 15 | 15 | 100% |
| B P1#16-57 | 42 | 42 | 100% |
| B P2#58-125 | 68 | 68 | 100% |
| B P3#126-156 | 31 | 31 | 100% |
| A 优化 0-18 / 根因 1-10 / 5 假数据 | 见 §7 | 见 §7 | 见 §7 |
| B 方案 1-13 / 5 根因 | 见 §8 | 见 §8 | 见 §8 |

### 0.1 实施进度（S1 起逐任务登记）

| 任务 | 内容 | 状态 |
|---|---|---|
| S0.1 | 基线测试收集 | ✔ 完成（540 unit / 119 integration / 319ms import） |
| S0.2 | safe_action 破坏性命令防护 | ✔ 完成（13 单测 + ruff/mypy 通过） |
| S0.3 | CODING_STANDARDS + AST 检查 + CI 门禁 | ✔ 完成（`check_coding_standards.py src` = OK） |
| S1.1.1 | deep_merge 深拷贝 + 配置不可变 | ✔ 完成（utils/py_test 深拷贝单测 + application_service 去就地改写 + 消费方测试） |
| S1.1.2 | 文件日志 + GUI 强制接入 | ✔ 完成（RotatingFileHandler 5MB×3 + stderr 兜底 + 级别校验回退 INFO，4 单测） |
| S1.1.3 | step3_fields 统一 lxml.html | ✔ 完成（3 处 fromstring + getpath 修正 + suggest_xpath_candidates 纯函数，5 单测） |
| S1.1.4 | `_()` 提到模块顶层 | ✔ 完成（headless 冒烟测试 + i18n 无 Qt 依赖确认） |
| S1.1.5 | QThread 销毁与 closeEvent | ✔ 完成（pdf_workbench closeEvent + change_monitor 并发守卫 + _AIEnrichWorker deleteLater + cancel_all 单预算单测） |
| S1.2.1 | drain 提到 finally + 通用异常分支 | ✔ 完成（_run.py 定义前置 + except/finally 双兜底 + _run_stream 补 summary，test_exception_mid_loop_drains_in_progress_rows） |
| S1.2.2 | reprocess 记录构造纳入 try | ✔ 完成（构造失败计入 failures 继续下一条，test_reprocess_survives_corrupt_single_record） |
| S1.2.3 | max_pages=0 语义修复 | ✔ 完成（<1 显式 ValueError，_run.py:95） |
| S1.2.4 | 独立 attempted 硬上限 | ✔ 完成（max_requests 默认 limit×5 + <1 报错 + 提交计数，test_max_requests_hard_caps_total_dispatch + test_negative_max_requests_rejected） |
| S1.2.5 | safe_data 工具函数库 | ✔ 完成（safe_json_loads/int/float/get/slice/bool 6 函数 8 单测；替换 extractors/streams/browser_fetcher/provenance/markdown_exporter/ai_graph/ai_task_designer/scheduler/security_audit 9 处裸解析） |
| S1.3.1 | CSV 注入护栏 lstrip 前缀 | ✔ 完成（utils.excel_safe lstrip("\t\r\n ") 捕获 \t=cmd/\r@x，4 前缀单测） |
| S1.3.2 | 归档安全（. / ./ · 盘符 · 峰值原子写） | ✔ 完成（_safe_relative 判空 parts 拒 "."；archives._safe_relative_path 同步；copy_zip_member 临时文件 + os.replace 原子化，5 单测） |
| S1.3.3 | request_payload 脱敏 | ✔ 完成（redact_payload 递归脱敏 token/key/password/authorization；模板与报告同步脱敏，2 单测） |
| S1.3.4 | browser 跨来源认证头剥除 | ✔ 完成（strip_cross_origin_credentials 纯函数 + _guard_route 注入 target_url，跨源 continue_(headers=stripped)，3 单测） |
| S1.3.5 | AsyncClient DNS 固定 | ✔ 完成（_PinnedAsyncNetworkBackend 把 httpcore 后端换成批准地址字面量 + proxy 过 require，3 单测） |
| S1.3.6 | Playwright 响应字节预算前置 | ✔ 完成（content-length 预检 + 总预算检查后再读 body，3 单测） |
| S1.3.7 | 插件动态 metadata 绕过封堵 | ✔ 完成（运行时 permissions ⊆ 静态字面量审批集，动态/拼接 metadata 一律拒绝，2 单测） |
| S1.3.8 | 密钥环境变量前缀统一 | ✔ 完成（新前缀 OMNICRAWL_SECRET_* 优先，兼容旧 OMNICRAW_SECRET_*，1 单测） |
| S1.4.1 | 价格分类正则 `$` 转义 | ✔ 完成（`\$` 转义 + 非价格元素不再误判，4 单测） |
| S1.4.2 | TemplateLoader.combine 实现 | ✔ 完成（seed_urls/fields 合并去重、后者显式值覆盖、缺失模板报错，6 单测） |
| S1.4.3 | SSE EOF 忙循环 | ✔ 完成（连续 3 次空读判定断开退出，正常空行事件流不受影响，1 单测） |
| S1.4.4 | 字段名进 XPath 参数化 + 选择器语义统一 | ✔ 完成（XPath 变量绑定防引号崩溃 + XPathEvalError 兜底 + 可视化导入按 selector_kind 分流，3 单测） |
| S1.4.5 | analyze_to_config 契约核验 | ✔ 完成（attr 替代 attribute、item_selector 对齐、pagination 输出 page/parameter、占位 URL 拦截，3 单测） |
| S1.5.1 | PDF 解析每线程独立 DB + 短事务批写 | ✔ 完成（threading.local 每线程连接 + 解析/写库分离单事务 executemany×500；修复 sqlite3.Row 无 .get 与异常时线程连接泄漏；2 单测含并发无 BUSY + parse_dead 阈值） |
| S1.5.2 | Pipeline 构造回滚 + close 异常聚合 | ✔ 完成（ExitStack 逆序回滚已建资源；close 逐项 try/except 汇总 RuntimeError；3 单测：回滚顺序/聚合续跑/幂等） |
| S1.5.3 | requires-python 3.10 兼容 | ✔ 完成（9 文件 datetime.UTC→timezone.utc；test_help_button tomllib→tomli fallback；pyproject dev 增 tomli 条件依赖；ruff target-version 对齐 py310） |
| S1.5.4 | commands __all__ 懒加载导出 | ✔ 完成（__getattr__ 懒加载 + field_suggest 别名；3 单测 star-import/别名等价/未知属性） |
| S1.5.5 | Redis frontier 原子性 + fingerprint 去重 | ✔ 完成（list() 物化计数、MULTI/EXEC 三命令、zadd 成员用 fingerprint、seen/payload expire 7 天；7 测含生成器去重） |
| S1.5.6 | template_monitor None 防护 | ✔ 完成（content_type 归一 + record.data or {} 防御，实际修掉 dict(None) 崩点；1 单测） |
| S1.5.7 | EasySpider scroll 动作 + 点击 wait 语义 | ✔ 完成（browser_fetcher 新增 "scroll"→scroll_bottom(times)；点击 wait 改显式 wait_ms；4 单测覆盖 scrollCount>1/等待语义/scrollType=0/dispatch） |
| S1.5.8 | async 抓取器跨 loop | ✔ 完成（_client_for 按 running loop 建客户端与清理，去 set_event_loop；2 单测：per-loop 绑定复用 + 双线程并行） |
| S2.1.1 | DEFAULTS 单一真源 + 白名单校验 | ✔ 完成（① DEFAULTS 补 http.engine=urllib / processors.pdf.ocr_backend=none；② validate_config 顶层段白名单 + 固定段子键白名单 + storage.*/processors.pdf 嵌套白名单，未知键默认 warning、strict=True 升级 error，difflib 候选提示；③ GUI 序列化以"to_yaml 产物必须通过核心 load_config"契约测试替代 diff 式写出（避免破坏 passthrough round-trip）；④ validate_full_config 转调核心 validate_config（无 seeds 时跳过防误报）；⑤ to_yaml→load_config 契约测试；后项：capability quick 不再静默丢 require_features（源B P1#30）、http.engine 未知值显式报错（P2#75）、URL 补全不再产出 https://C:\...（F688）；19 单测） |
| S2.1.2 | 配置错误信息增强 | ✔ 完成（① load_config 校验错误改为 ConfigParseError 多行带编号 [N]，不再 ；join 挤一行；英文错误消息全部中文化；② YAML 语法错误包装含行列号 + 原始栈进日志（__cause__ 保留）；③ expand_env_checked 收集缺失 ${VAR}，load_config 汇总进 config.warnings；AppConfig 新增 warnings 字段；后项：describe_error 覆盖 urllib.URLError/SSL/空 message 兜底/KeyError 带键名（P2#65/#78）、LoginFailedError 可达建议（P2#70）、benchmark help 渲染锁 10% 且不因裸 % 崩溃（P0#16）；ConfigParseError 改 ValueError 双继承保持兼容；14 单测） |
| S2.1.3 | .env 优先级修正 + 解析健壮化 | ✔ 完成（① parse_env_file 重写：errors=replace 防乱码崩（P2#67）、BOM 兼容、export 前缀剥除、行内注释剥离；② _parse_env_value 首字符引号+配对闭引号，未闭合回退；③ load_ai_env 以 reversed(ai_env_candidates) 遍历实现 项目>cwd>用户级 优先级（P1#81/P1#18），os.environ 最后覆盖；④ source 备份导出同吃 export；后项：P2#67 收口；11 单测；另修 test_ai_env.py 既有测试 env 泄漏（sync_ai_env_to_os 直写 os.environ 绕过 monkeypatch.delenv 的记录，导致 'new-model' 泄漏污染后续测试）） |
| S2.1.4 | 重试配置双轨合并 | ✔ 完成（① DEFAULTS["http"] 新增 retry_on_status 单点默认（=RETRYABLE_STATUS，408/425/429/500/502/503/504），测试锁两处一致；② _apply_retry_alias：用户 YAML 显式写 retry_max 且未写 retries 时合并进 retries（retries 优先），0 表示不重试，load_config 阶段生效；③ validate_config 校验 retry_max 非负整数；④ http 段白名单放行 retry_max；⑤ parse_retry_config 的 max_retries 改读 retries、status_codes 默认统一 RETRYABLE_STATUS（原默认 [429,502,503,504] 与硬编码不一致），显式空列表=不重试任何状态码；⑥ http_client.fetch / async_fetcher._request 消费 retry_cfg["status_codes"]，弃用硬编码 RETRYABLE_STATUS；P1#29/#21/P2#68 + P3#133 收口；14 单测） |
| S2.2.1 | secrets_store 基础设施 | ✔ 完成（新建 core/secrets_store.py：AES-GCM 整包加密 + 原子写（tmp+os.replace），键名也密文化；主密钥 OS keyring 优先（keyring 包，SERVICE=omnicrawler），keyring 后端异常自动 fallback PBKDF2-HMAC-SHA256（600k 迭代，密码取 OMNICRAWL_MASTER_PASSWORD），不抛未捕获异常；无 keyring 且无密码时抛可读 SecretsStoreError；get/set/delete/keys API + 进程内缓存 + encrypt/decrypt blob API（S2.2.3 用）；9 单测覆盖 roundtrip/无明文/fallback/损坏文件/密钥不匹配/跨实例） |
| S2.2.2 | 六处出口统一加密 + 脱敏 | ✔ 完成（四出口 + 两出口补全，全六处闭环：① credentials.get_secret 增加 secrets_store 兜底 + 新增 seal_secret(name, plaintext) 密封 API（引用幂等、失败抛 SecretsStoreError 绝不回退明文，SecretsStoreError re-export）；② config_serializer.to_yaml 对 ai.providers.*.api_key 明文加密进 secrets_store、落盘写 secret://ai.<provider>.api_key 引用，autosave 复用自动覆盖；③ security_audit 提取 scan_config_text（finditer 多命中，跳过 secret:///${}/<redacted>）；④ WorkspaceManager.package 与 GUI export_config_package 导出前明文凭据扫描命中拒绝；⑤ settings.ini 代理池出口：_seal_proxy_list 对含 user:pass@ 的代理加密入 store、INI 只写 secret://settings.proxy_list 引用，AppSettings.proxy_list 解引用还原（失败返回空串），隐身对话框保存失败弹窗拒写明文、加载改走 public property；⑥ .env 出口：GUI _save_ai_config_to_env 对 OMNICRAWL_AI_API_KEY 先 seal_secret 再落盘（不可存弹窗拒绝），load_ai_env 对 secret:// 值统一解引用还原（os.environ 覆盖仍生效、引用不可解保留引用串不泄漏明文）；27 单测） |
| S2.2.3 | cookie 原子写 + 加密落盘 | ✔ 完成（fetching/session.py 重写：① 落盘改"临时明文文件+SecretsStore.encrypt 成 blob+tmp 原子 os.replace"，崩溃不残留损坏 cookie；② 加密失败告警并跳过落盘（会话仍驻内存，绝不写明文）；③ 加载失败 LOGGER.warning 显式告警不再静默 pass；④ 旧 LWP 明文格式向后兼容加载（按 FILE_MAGIC 判定格式）；PBKDF2#71/#72；7 单测覆盖无明文/还原/原子性/告警/legacy/加密失败跳过/no-path noop） |
| S2.2.4 | plan_compiler 脱敏补全 | ✔ 完成（① _redact_for_hash 掩码词表补 authorization/bearer/cookie 与中文密钥键名 密码/口令/密钥/令牌/凭据/授权，标记统一为 <redacted> 与扫描跳过前缀呼应；② commands/plan.py execute 导出（plan/diff）前 _redact_for_hash 脱敏 + YAML 化 scan_config_text 复核扫描，命中即拒绝；P1#47 收口；4 单测覆盖掩码/导出无明文/diff 脱敏/YAML 有效性） |
| S2.3.1 | OCR 多进程预检与降级 | ✔ 完成（ocr.py：多进程路径进入进程池前本进程 create_backend 预检，失败/None 按 D13 语义整批标记 skipped + errors 表写"依赖缺失"（串行路径原有降级保留）；预检实例即刻释放不驻留模型；ProcessPoolExecutor 外层 try/except（BrokenProcessPool/Exception），崩溃后剩余页标记 skipped + errors，管线不崩；temp 温度保护/批提交保留；P1#84/B53 收口；4 单测覆盖串行降级/多进程预检降级/backend none/池崩溃） |
| S2.3.2 | LLM 客户端构造容错 | ✔ 完成（extraction_stage 的 create_llm_client 包 try/except：构造失败（Key 空/参数非法）记 warning、client=None、纯规则模式继续（extract_document 既有 None 分支）；P1#85/B52 + 根因 3；1 单测验证 client=None 透传） |
| S2.3.3 | Tesseract 语言归一 | ✔ 完成（ocr.py 新增 normalize_ocr_lang：ch/chi/cn/sim→chi_sim、trad→chi_tra、jp/jap→jpn，+ 分隔逐段归一、未知语言保留、空默认 chi_sim+eng；TesseractBackend.__init__ 应用；P1#86/B48 收口；1 单测 11 断言） |
| S2.3.4 | service 阶段隔离补全 + GUI 失败识别 + failed 短路 | ✔ 完成（① run_processing 的 ocr/text_export 补 try/except 与 ingest/parse 一致（失败记 failed+error、stopped 短路后续阶段）；② pdf_workbench._on_done 用 _collect_failures 递归检查全部阶段 failed/stopped（含 processing 嵌套），有失败显示"⚠ 部分阶段失败"、结果面板列失败清单、toast 警告，不再显示"✓ 全部完成"；③ pdfx cli 的 run/process 手写阶段链统一走 service.run_extraction/run_processing（阶段隔离/短路/降级语义一致），run_extraction 补 ocr_workers 透传；B24/B47/B100 + P1#89 收口；3 单测 + cli 集成测试适配） |
| S2.3.5 | PDF 计数口径与导出结构（附带） | ✔ 完成（① pdf_integration 扫描改 rglob 递归，子目录 PDF 计入；② GUI 导出文件遍历改 files.values() 显示真实路径（原遍历 dict 键）；③ _pdf_input_dir 共享辅助：显式配置 storage.objects.local_directory（非默认 "."）时以配置驱动，不再被硬编码 artifacts/pdf 绕过；B72 收口；3 单测） |
| S2.3.6/7 | pdfx 类型白名单补全 | ✔ 完成（pdfx/config.py 白名单补 boolean/entity/relationship——normalization.py 对应分支（boolean 真值/relationship 别名精确匹配 D53/entity EntityResolver D44）已可达，load_config 接受并走对应分支，entity_master_csv 链路经 EntityResolver.from_config 恢复；A88 收口；1 单测验证三类型 from_dict + 归一分支） |
| S2.4.1 | 0 条也算失败 + 三态语义 | ✔ 完成（① core/run_state.py：partial_success 独立终态（RUN_STATES/TERMINAL_RUN_STATES/ALLOWED_TRANSITIONS running→partial_success），STATUS_ALIASES completed_with_errors 不再映射 succeeded 而映射 partial_success——P1#23/B23 收口；② commands/run_task.execute 注入 effective_records 恒输出 + exit_code 三态（failed/cancelled→1，strict 下仅 succeeded 且有效记录>0 才为 0，partial_success strict 下 1、默认 0），0 条无条件打印引导提示（无数据/被拦截→doctor/模板不匹配）——假绿灯 0 条分支收口；③ cli/_handlers.py _run_run_or_resume 落 raise SystemExit(result.exit_code)（run 退出码落地）、_main.py run/resume 加 --strict flag；④ worker_task_runner _poll 识别 partial_success（finished+rc0+warn）且 succeeded 0 条显示告警不再哑成功——B88 收口；9 单测） |
| S2.5.1 | 金额单位匹配修正 | ✔ 完成（normalization.py：AMOUNT_UNITS 补 千万元=10^7/百万=10^6/百元=100 并置于"万元/元"之前防子串抢先；外币检测升级正则 _FOREIGN_CURRENCY_RE：中文币名 + ISO 前后缀（USD 100/200 USD）+ 符号（$100/€50/£/HK$），¥ 不拒；P1#28/#20/P2#102 收口；3 单测） |
| S2.5.2 | reprocess 幂等导出刷新 | ✔ 完成（_run_exports 加 force 参数：reprocess 路径绕过幂等提交缓存强制重导出；StateStore.begin_export 加 force，succeeded 提交降回 running 重跑；P1#78/#45 收口；1 单测） |
| S2.5.3 | state_store claim 原子化 | ✔ 完成（claim 改为 候选 SELECT 排序 + 条件 UPDATE（WHERE status='pending'）逐条原子认领，rowcount 判胜；并发抢走的行跳过重取，杜绝 SELECT→UPDATE 双重认领；P1#26/#51 收口；2 单测） |
| S2.5.4 | 流式模式参数透传 | ✔ 完成（_run_stream 接收 max_pages/callback/should_stop：should_stop 在 seed 循环与 long_poll 消息循环内生效（取消即收尾+egress disconnect），stream_progress 进度事件按消息数回调（processed/limit/messages），long_poll 取消标志 break 外层；P1#26 收口；2 单测） |
| S2.5.5 | crawl4ai 走 EgressBroker + 指纹含 headers | ✔ 完成（① Crawl4AIEngine 注入 egress：_authorize 走 broker.authorize（审计/预算/熔断），_record_result 记响应字节/成功/失败，全部入口（fetch/fetch_async/fetch_many/adaptive/deep）接入，无 egress 时保留轻量直连校验；② CrawlRequest.fingerprint 含规范化 headers（顺序无关、单向摘要）——多语言/多身份不再误去重；③ _convert metadata None 兜底空 dict 防 .get 崩溃；④ status_code 真实透传（404/403 保留，0/None 才回退 200）；P1#48/#33/#46 + 根因 4 收口；8 单测） |
| S2.5.6 | 压缩解码补全 | ✔ 完成（_decode_content 支持 br（brotli）/zstd（zstandard）：已装解码库时 Accept-Encoding 附加声明；未装包时显式 ValueError 告警（不再把压缩字节当正文）；解压后超限仍拒；P2#69 收口；5 单测 + 1 skip） |
| S2.5.7 | sources seed 保留全部请求 | ✔ 完成（seed() 分页逻辑遍历全部 seed 请求逐一展开，不再只保留 requests[0] 丢弃其余；多 seed 配置全部分页、无静默丢失；P1#32 收口；1 集成测试） |
| S2.5.8 | CookieSession 线程安全 | ✔ 完成（新增 _ThreadSafeCookieJar：add_cookie_header/extract_cookies/save/load/_really_load 统一锁，与 save() 共用同一把锁语义，进程级单例 jar 并发读写无竞态；P1#44 收口；3 单测） |
| S2.5.9 | async Retry-After 封顶 | ✔ 完成（async_fetcher 重试分支 Retry-After 封顶默认 60s（http.retry_after_cap_seconds 可配），超限 LOGGER.warning 告警不再静默睡 2 小时；P1#73 收口；4 单测） |
| S2.5.10 | routing SPA/挑战页检测修正 | ✔ 完成（SPA 根节点正则放宽：id=app/root/__next/__nuxt 容器含子元素同样命中（不再要求空根）；挑战特征词收窄：强特征（cf-chl-/cloudflare ray id/just a moment 等）全前缀命中即判，弱特征（access denied/captcha）仅可见文本（剥 script/style）命中才判；P1#97/P2#73 收口；6 单测） |
| S2.5.11 | browser fetch 超时取消机制 | ✔ 完成（_PoolTask 加 discarded 事件；PlaywrightPool.fetch 超时置位丢弃并抛 TimeoutError；_handle_task 统一处理：丢弃任务不渲染直接 error，渲染期间被丢弃则关闭对应 context 并从池移除防滞留；P1#98 收口；4 单测） |
| S2.5.12 | Selenium 默认可用 + BiDi 异常放行 | ✔ 完成（egress.experimental_selenium_bidi_guard 默认 True（安全默认，不再默认 raise），显式关闭时给出风险提示；BiDi guard 对非 PermissionError 异常（预算/熔断/瞬态）放行请求而非挂死渲染；P1#96/#99 收口；5 单测） |
| S2.5.13 | browser 配置代理 context 键修复 | ✔ 完成（_new_context 与 _context_key 同源：meta 代理优先、否则 config.http.proxy，配置代理对 Playwright 生效、会话隔离按代理区分；P1#95 收口；4 单测） |
| S2.5.14 | extractors 正则/JSON 容错 + field_designer 性能 | ✔ 完成（safe_regex_search：编译错误/嵌套量词（灾难性回溯）执行前拒绝；match.group(group) 越界防护（IndexError/re.error → 跳过）；JSONProcessor 错误带 URL 上下文；field_designer 节点上限 5 万 + len(element) 替代 iterdescendants 全树遍历（O(n²)→O(1)）；P1#42/#100/#101/P2#116 收口；7 单测） |
| S2.5.15 | ai_graph 空 choices 防护 | ✔ 完成（choices 非 list/空时记 warning 按空内容降级，不再 IndexError；P1#43 收口；3 单测） |
| S2.5.16 | record_sinks fail_open 改 fail_closed | ✔ 完成（DEFAULTS 与 build_record_sink_manager/RecordSinkManager 默认 fail_open=False，sink 崩坏使运行失败不再静默丢记录，显式 fail_open 配置保留；P1#41 收口；3 单测） |
| S2.5.17 | workspace 流式打包 | ✔ 完成（_full_package 改逐文件流式写出（zip 内 1MB 块 + 边算 sha256），排除 SQLite（快照替代）与 output 旧导出；多 GB 工作区内存可控；P1#39 收口；2 单测） |
| S2.5.18 | offline_demo 合法 PDF | ✔ 完成（PyMuPDF 生成合法 PDF：report.pdf 带文字层可直抽文本，scan.pdf 渲染为纯位图页无文字层（OCR 演示路径真实可走通）；P1#40 收口；2 单测） |
| S2.5.19 | scheduler finish KeyError + lease 缩短 | ✔ 完成（finish 对已删调度静默兜底不中断 run_due 循环；claim_due 默认租约 3600s→300s 快速回收；P1#35 收口；3 单测） |
| S2.5.20 | recovery mkdir 同秒冲突 | ✔ 完成（quarantine 目录加随机后缀 + exist_ok，同秒两次 reset 不再 FileExistsError；P1#36 收口；1 集成测试） |
| S3.1.1 | BackgroundWorker 基类 + 6 处阻塞点改造 | ✔ 完成（新建 gui/core/background_worker.py（QThread+succeeded/failed 信号+取消+自动清理+run_worker 接线）；env 探测重试、pip 自动安装（delegates/env_checker）、run_preflight（main._show_preflight→_apply_preflight）、Markdown 导出（result_table）、PDF 目录扫描（pdf_workbench._scan_directory→_apply_scan_result）、PyMuPDF 打开/渲染（pdf_region_selector）全部移入后台线程；P1#50/#51/#61/B1#22+根因2 收口；5 单测） |
| S3.1.2 | 导航/向导/状态机常量化 | ✔ 完成（NavIndex 常量类独立模块 gui/navigation.py（delegates↔main 循环导入规避）；修复"结果与复核"错页（open_results 原误用 MONITOR 行号）；_rebuild_wizard 改用 self._wizard_splitter.replaceWidget（原操作外层 layout 导致新向导不显示）；托盘 QMenu(self) 接管所有权；全处魔法行号替换；P0#15+P1#60/#90/#91/B1#19 收口） |
| S3.1.3 | 运行历史记录修正 | ✔ 完成（run_task 启动时存 mw._running_task_id，on_task_state_changed 结束用该 id 更新历史并清空，运行中切换配置不串记录；P1#52/B1#40 收口） |
| S3.1.4 | Toast 死区与内存治理 | ✔ 完成（ToastOverlay 高度按内容 sizeHint（不再全高 360px 死区）；show_toast 返回标注 Toast|None 一致；LogConsole._all_logs 裁剪 5000 行（MAX_CACHED_LOGS）；P1#63/#68/B2#97 收口） |
| S3.1.5 | 无托盘静默停止防护 | ✔ 完成（closeEvent 无托盘且运行中弹三选一：停止并退出/最小化到后台/取消，不再静默 stop()；P1#94 收口） |
| S3.1.6 | PDF 工作台取消态修复 | ✔ 完成（worker 取消路径统一发 all_done（带 stopped/cancelled 标志）UI 恢复可操作；_cancel 等待线程结束并清理注入环境变量（PDFX_LLM_API_KEY 无残留）；P1#56/B1#34 收口） |
| S3.1.7 | gui/main 顶层副作用移除 | ✔ 完成（configure_runtime_environment 迁入 main()；手写 --help 打印块删除（argparse 自带）；sys.argv 扫描改只读 _cli_mode() 判定；import gui.main 无环境/argv/退出副作用；B2#87 收口） |
| S3.1.8 | WorkerTaskRunner.start 残留清理 | ✔ 完成（backend.start 失败时删除残留 _yaml_path 并置 None；B2#89 收口） |
| S3.1.9 | result_table 导出对话框接线 | ✔ 完成（Excel 导出 QProgressDialog.canceled 连接 ExportThread.requestInterruption，取消真正中断；Markdown 导出已在 S3.1.1 后台化；P1#62 收口） |
| S3.1.10 | pdf_region_selector 异步渲染 | ✔ 完成（S3.1.1 已覆盖：PDF 打开/渲染均走 BackgroundWorker；P1#57 收口） |
| S3.1.11 | env_checker 取消回退提示 | ✔ 完成（取消自定义数据目录选择后不再静默回退 portable——保留数据模式并 Toast 提示；P1#58 收口） |
| S3.1.12 | help_center 未知 id 防护 | ✔ 完成（show_help 未知 id 不写 _current_id（仅已知 id 写入），复制示例不再 KeyError；P1#59 收口；1 单测） |
| S3.1.13 | help_dialog tmp 清理 | ✔ 完成（选择器指南临时文件 60s 延迟删除（QTimer.singleShot + unlink missing_ok），不再每次泄漏；P1#65 收口） |
| S3.1.14 | error_dialog 正则误伤 | ✔ 完成（脱敏正则改为仅凭据上下文（?key=/&token= 等后参数值替换 [REDACTED]），普通问号原样保留；P1#66 收口；3 单测） |
| S3.1.15 | theme 导航常量 | ✔ 完成（theme.py 固定行号 2/5/6 改 NavIndex.PDF_WORKBENCH/RESULTS/EVIDENCE；P1#64 收口） |
| S3.1.16 | stealth_settings 公共属性 | ✔ 完成（AppSettings 加公共 value()/set_value()，stealth_settings 全部改走公共接口，移除私有直读写；P1#70 收口） |
| S3.1.17 | pdf_region 基序统一 | ✔ 完成（extract_region/make_region_rule 页码统一 1 基（边界转换），rule.page 不再 +1 不一致，GUI 调用同步修正；P1#71 收口；3 单测） |
| S3.1.19 | pdf_workbench rglob 后台化 | ✔ 完成（S3.1.1 已覆盖：rglob+stat 走 _ScanWorker；P1#55 收口） |
| S3.1.20 | step3 get_selections 后台化 | ✔ 完成（高级可视化选择改 BackgroundWorker 限时轮询（30s）+ 可取消，超时提示不再假死；server.stop 在 worker 内收尾；P1#54 收口） |
| S3.1.21 | async_workers max_rows 参数 | ✔ 完成（CsvIndexWorker.__init__ 补 max_rows（None=完整计数，>0 提前停止），调用处不再 TypeError；P1#93 收口） |
| S3.1.22 | design_system 字体缩放 | ✔ 完成（apply_font_strategy 移除 0.75 稀释魔数，字体=body×factor，80–160% 缩放真实生效；P1#92 收口） |
| S3.1.25 | AutosaveManager 后台写盘 + 失败提示 | ✔ 完成（save_now 后台线程写盘（daemon），新增 save_failed 信号失败可见，interval_ms 可配置；B2#94 收口） |
| S3.1.26 | 配置历史恢复先校验 | ✔ 完成（恢复前 load_yaml+validate_full_config，坏配置不覆盖当前文件并报错；B2#95 收口） |
| S3.1.27 | switch_project 组件重建 | ✔ 完成（依赖项目根组件抽取 _build_project_components（config_history/task_runner/autosave/template_loader/task_history/resource_monitor），switch_project 调用 _rebuild_project_components 重建并 deleteLater 旧组件；B2#96 收口） |
| S3.2.1 | 接线类孤儿代码 | ✔ 完成（① value_pattern 经 pdfx.safe_regex.validate_pattern 编译校验（病态模式拒绝）；② history_max_entries/history_max_days 消费方接入 TaskHistory 构造（main 传 settings 值），显示截断不再误删文件中旧记录（内存上限 MAX_LOADED_RECORDS=5000）；③ assert_no_raw_hex 已在 design_system.apply 无条件启用；④ ChangeDetector 基线持久化（data_dir/baselines.json 原子写，add_rule 恢复 last_hash），GUI 移除 "__baseline__" 哨兵假哈希——每轮真实哈希比较不再误报；⑤ validate_ai_output 改为存在键校验语义并接入 ai_graph._parse_response（未知键/类型错误降级）；⑥ 每规则变更历史有界 50 条 + _save_rules 失败 Toast 可见；P1#69/B2#107/#112+根因7 收口；2 单测） |
| S3.2.2 | 标注类孤儿代码 + 消费方存在性测试 | ✔ 完成（fetching/archives 模块与 safe_extract_archive 标 deprecated（DeprecationWarning+文档）；AIGraphExtractor/ProxyRotator/apply_to_playwright_context 标"实验性，不在主路径"；新建消费方存在性测试 tests/unit/core/test_consumer_existence.py（11 个守卫/配置项 token 零消费即红）；根因7+防回归 收口；13 单测） |
| S3.2.3 | 归档单实现 + 新增未知文件检测 | ✔ 完成（归档安全已收敛：core/archive_security 被 component_manager/updater 生产消费，fetching/archives 零生产调用+deprecated；verify_runtime_manifest 增加 unknown 检测（磁盘存在但清单未声明文件，含 sort+ok 判定），DLL 侧加载旁路物可检出；B2#105/#106 收口；2 单测） |
| S3.3.1 | CLI 输出快照驱动测试 | ✔ 完成（新建 tests/fixtures/cli_outputs/ 5 套快照（正常/失败/0条/异常/被拦网络）+ tests/integration/test_cli_gui_contract.py 断言 LogParser 解析；修复真实契约问题：① 统计正则匹配真实格式（"提取记录: 45"/"采集页面: 3"/"下载附件: 2"）；② 显式级别前缀（WARNING:/ERROR:）优先于内容关键词（PermissionError 的 error 子串不再误判）——进度条恒 0% 根因收口；B2 GUI↔CLI 契约簇 收口；6 集成测试） |
| S3.3.2 | 配置往返 e2e + 结构化证据测试 | ✔ 完成（新建 tests/integration/test_config_round_trip.py：GUI CrawlConfig→save_yaml→核心 load_config→Pipeline run 产记录往返；10 个拼写错误用例 strict 全拦截；export_single_record 三样式+dict 证据路径参数化；**顺带修复真实缺陷**：validate_config section_whitelist 漏检 source 段（seedz 等拼写静默通过）——补全 source 白名单（含 method/headers/payload/pagination/login/max_messages/subscribe/query 等消费键）；P0#16 守护+B2 契约簇 收口；14 集成测试） |
| S3.4.1 | 导出器修正 | ✔ 完成（① jsonl 剔除 data_json/evidence_json 原始列（同一数据不再写两遍）；② CSV 列按 extract.fields 定义顺序 + 首次出现序（不再字母排序）；③ parquet 保留原始类型（pyarrow 推断不再全 str）、duckdb 按样例推断 INTEGER/DOUBLE/BOOLEAN/VARCHAR（_infer_column_type，全 NULL 回退 VARCHAR）；④ CSV 写出改 generator 流式；⑤ responses.csv/errors.csv 文件受 outputs.csv 开关约束（数据仍加载供 xlsx 内嵌 sheet）；⑥ xlsx 缺 openpyxl 告警（S2.5.24 已做）；⑦ xlsx 单表 100 万行上限截断 + 告警；⑧ xlsx 写失败（文件占用）转可读 RuntimeError；B2#81-85+P1#46+P3#127 收口；4 单测） |
| S4.1 | 包根惰性化 | ✔ 完成（① 删 _setup_compat_aliases eager 调用（原 287ms 全量导入）；② 模块级 __getattr__ 惰性重定向保留；③ 新增 _CompatMetaFinder（PathFinder 物理存在性校验——与真实子包同名的 quality/utils/state 不被拦截，旧名不存在才走别名；_AliasLoader 壳命名空间逐键复制，显式 import 旧名可用）；④ 兼容模块加载失败 logger.warning；⑤ import omnicrawler 20ms 断言测试（阈值 50ms，零顶层子模块 eager 加载）；⑥ pipeline/registry.py 重量级 fetcher（browser/async）下放到 build_registry 内 import；适配 2 处旧名 patch 测试改新名（patch 走 import_module 得壳）；根因8+B2#77+P3 启动耗时簇 收口；4 单测） |
| S4.2 | 默认路径治理 | ✔ 完成（① CLI --config 已全 required（验证测试断言主要命令含 plugins 例外）；② pdfx 默认路径已相对项目目录（config 驱动，不指向安装目录）；③ CLI 启动第一行日志打印 data_dir（portable_data_root）与 config_path；④ --legacy-data-dir 无对应遗留路径实现（跳过）；⑤ GUI 项目根兜底不再用 cwd（冻结=数据根，源码=application_dir）；⑥ load_config 支持显式 project.root 覆盖探测（pyproject→configs 确定性规则保留）；根因9+B2#63/#90+P3 默认路径簇 收口；4 单测） |
| S4.3.1 | 破坏性命令统一防护 | ✔ 完成（workspace rollback / recovery rollback-config / components uninstall|rollback 三个 handler 接入 require_explicit_apply（未带 --apply/--yes 抛 ConfirmationRequiredError 不执行任何写入）；三个子命令 parser 各加 --apply/--yes flag；require_known_stage 已有（choices 限定的命令由 argparse 拒绝未知值）；根因10+B2#101 收口；9 单测） |
| S4.3.2 | i18n 链路修复（"假语言包"） | ✔ 完成（① domain 统一为 omnicrawler-gui（原 "omnicrawler" 与 locale/omnicrawler-gui.pot 不匹配——切换语言永不生效根因）；② 新建纯标准库 tools/compile_mo.py 编译器（GNU .mo 格式：length 在前表项、str 区偏移、尾部 NUL、跳过 msgid_plural、多行字符串 filling 标记），生成 en_US .mo（541 条目，gettext 加载+翻译验证通过）；③ 新建 i18n gate 测试（domain 匹配 / .mo 可加载 / 真实翻译生效 / gui 源码中文字面量非 _() 包裹即红）；④ 全 gui 源码 ~450 处中文字面量包 _()（core/views/widgets/wizard/delegates/runner/main/home/design_system 等，含 f-string 与隐式拼接 + 修正），补 _ import 全部模块；顺带修复误 checkout 丢失的 config_serializer S2.2.2 secrets 密封逻辑（重建 _seal_ai_provider_keys + 模块级 SecretsStore 契约）；实现 S2.1.1 ④ validate_full_config 核心转调（_core_validate 深合并 passthrough+user_agent 走核心 validate_config，修复 2 个基线失败测试）；P1#102+"假语言包" 收口；4 单测） |
| S4.3.3 | 打包/启动脚本修复（第一批） | ✔ 完成（① prepare_windows_runtime.ps1 顶部新增 $KNOWN_SHA256 登记表 + Get-Asset -RequireKnownHash fail-closed（无已知哈希拒绝下载第三方二进制，脚本内 PS 语法+行为测试验证）；② spec 已启用 disable_windowed_traceback=False（OmniCrawler.spec + Standard.spec 均含）；③ OmniCrawler-Launcher.bat 失败分支补日志路径提示（%~dp0logs\，GBK 字节级追加不破坏中文）；④ prepare_windows_runtime.ps1 补 UTF-8 BOM 兼容 PS5.1（无 BOM 中文注释乱码导致解析失败根因）；P1#103/#108+P3 打包簇 收口） |
| S4.3.4 | 打包/启动脚本簇（第二批） | ✔ 完成（① add_template_version.ps1 去除硬编码绝对路径（E:\...VScode project 3\...），改为 $PSScriptRoot 推导仓库相对路径 + -Base 参数；② build_windows.ps1 / install_windows.ps1 顶部加架构断言（32 位 OS/进程即 throw），install 已用 py -3 显式版本；③ run_workbench_windows.bat 末行转发 %*；④ 全部 4 个 PS1 补 UTF-8 BOM（PS5.1 中文注释兼容），全部经 PowerShell Parser API 语法校验通过；P1#104-107 收口） |
| S4.4 | pdfx 样板反向输出 | ✔ 完成（① extraction 每线程 DB→parser.py 已由 S1.5.1 覆盖；② safe_regex→validation.value_pattern 已由 S3.2.1 覆盖；③ pdfx/exporter.safe_cell 统一委托 core/utils.excel_safe（新增 max_length 截断 + None→"" + 非纯数字 =/+/@/- 前缀转义），消除重复实现；④ pdfx ingest D45 path+size 命中补 SHA-256 校验（同路径同大小新文件被检出 F699，命中项才哈希保 D45 收益；内容变化时删除旧 documents 行防 sha256 UNIQUE/source_path 关联冲突，document_sources ON CONFLICT 更新 doc_id），workspace 整读已由 S2.5.17 流式化覆盖；⑤ CODING_STANDARDS.md 新增 §9 子系统间整改样板迁移清单（唯一实现表 + 迁移规则）；P0#13+P1#87+B1#50+F699 收口；1 单测） |
| S4.5 | P3 批量清理（31 条） | ✔ 完成（P3#126 canonicalize_url 保留 userinfo；#127 excel_safe 数字正则补科学计数法；#128 utcnow 微秒精度；#129 迁移旧键清理（seed_urls/output/delay_seconds/crawl.pagination/item_selector/plugin_paths 迁移后删除，测试更新为新语义）；#130 已评估（现有代码无 ResponseTooLargeError 被 ValueError 吞路径）；#131 backoff jitter 叠加后封顶；#132 响应头多值 get_all 逗号合并（兼容 dict mock，_flatten_headers）；#133 已由 S2.1.4；#134 已评估（seed 处理无 or 吞 0 场景）；#135 _emit hook_fail_open 构造期只读一次；#136 choose_processor 只调一次（auto 模式复用判定结果）；#137 enrich 增加 extract.enrich 开关；#138 失败路径 summary 补 stats 与成功结构一致；#139 已评估（导出默认值省略语义合理）；#140 已评估（无共享异常实例）；#141 browser pool 超档位上限显式日志不再静默截断；#142 已评估（PDFX_OCR_BACKEND 单处读取）；#144 实体表缓存键加 mtime（CSV 变更感知）；#146 压缩比检查仅对大成员（≥16KB）生效；#148 已由 configure_data_mode cache_clear 修复；#149 verify_runtime_manifest 校验 format==1；#150 capabilities keyring 文案按平台（Win/macOS/Linux）；#151 已由 S2.5.31；#152 _terms 结果缓存（每条记录不再重复归一化）；#153 评分过滤剔除纯基础分（<=0.25）；#154 crawl4ai available 失败不缓存；#155 已评估（onnxruntime 全局日志设置不可避免、懒加载一次）；#156 已由 S2.5.42；1 测试更新） |
| S2.5.21 | execution_backend 会话权限 | ✔ 完成（IPC 安全核心为随机 auth_token（连接须 authkey 匹配），不依赖 chmod 0600；启动超时错误信息兜底非空（含最后错误或可读指引）；P1#37 收口；3 单测） |
| S2.5.22 | doctor 探测走 EgressBroker | ✔ 完成（_probe_models 传入 AppConfig 时经 build_safe_opener+EgressBroker 探测（策略/审计/预算受约束，私有 base_url 被拒），不再 urllib 直连；P1#38 收口；3 单测） |
| S2.5.23 | import-easyspider --ir 生效 | ✔ 完成（handler 读取 --ir 输出 Task IR JSON 而非 YAML，不再静默 no-op；P1#45 收口） |
| S2.5.24 | exporters xlsx 静默 pass | ✔ 完成（xlsx 缺 openpyxl 时 LOGGER.warning 显式告警（与 parquet/duckdb 一致），不再静默丢弃；P1#46 收口） |
| S2.5.25 | redis fingerprint 去重 + seen expire | ✔ 完成（既有实现已覆盖：zset 成员用 fingerprint 去重 + seen/payload expire TTL；P1#80/#110 收口） |
| S2.5.26 | AutoPilot 双向调整 | ✔ 完成（maybe_adjust 健康时也触发 propose 允许并发回升（severity info 判健康）；run_state 磁盘保护提案不再被应用循环静默丢弃（running→pause 生效）；P1#79/P2#109 收口；3 单测） |
| S2.5.27 | resources rglob 缓存 + audit 裁剪 | ✔ 完成（_directory_size 目录 mtime+文件数指纹缓存；AutoPilotState.audit 200 条/AdaptiveController.audit 500 条有界；P1#75 收口；3 单测） |
| S2.5.28 | markdown_exporter dict evidence | ✔ 完成（_render_card 对 dict 证据 str() 安全截断，不再切片 KeyError；P1#77 收口；2 单测） |
| S2.5.29 | stealth_enhanced 一致性 | ✔ 完成（sec_ch_ua 版本号与 UA Chrome 版本联动、非 Chromium UA 不注入；时区 offsets 表补全全部 _TIMEZONES（America/Chicago/Los_Angeles 等）；new_page() 不再建空白标签页（用现有页）；默认探测 IP 子项无对应实现跳过；P1#74 收口；4 单测） |
| S2.5.30 | quality_report 容错 + SQL 参数化 | ✔ 完成（evidence/data 用 safe_json_loads，畸形/NULL 单元格跳过并计数 skipped_malformed；run_id 已参数化无注入面；P1#34 收口；2 单测） |
| S2.5.31 | 主题匹配递归 + filter 深拷贝 | ✔ 完成（嵌套 dict/list 字段递归参与匹配；逐字段独立匹配不再空格拼接（跨字段假命中消除）；filter_records deepcopy 后再写 _topic_match 不污染调用方；P2#114/#115 + P3#151 收口；6 单测） |
| S2.5.32 | TableProcessor 尊重 extract.fields | ✔ 完成（fields 规则支持 {column: 表头名|索引} / {selector: td.x} / 字符串简写=表头名，未点名列剔除；无 fields 保持旧行为；P2#117 收口；4 单测） |
| S2.5.33 | 提取异常阶段归类 | ✔ 完成（新增 ExtractionError，_handle_result 提取块整体隔离；consume 识别后记 stage="extract" + "提取失败"日志 + extract 指标，不再一律 fetch；P2#118 收口；1 单测） |
| S2.5.34 | discover_links 去重/协议过滤 | ✔ 完成（链接 casefold 去重；javascript:/mailto:/tel:/sms:/data:/file:/about:/void(0) 伪协议过滤；P2#119 收口；2 单测） |
| S2.5.35 | export 空库检查 | ✔ 完成（ApplicationService.export 在 state.sqlite3 不存在时 FileNotFoundError 提示先跑采集，不再静默建空库；P2#120 收口；1 单测） |
| S2.5.36 | run_finished 事件兜底 | ✔ 完成（ApplicationService.run 异常路径也发 run_finished（finally 中），监听方不再永久等待；P2#121 收口；2 单测） |
| S2.5.37 | 增量统计替代全表聚合 | ✔ 完成（循环内 gauge 用 state.pending_count() 轻量索引 COUNT 替代 stats() 五表聚合；P2#123 收口） |
| S2.5.38 | retry_failed 分页 | ✔ 完成（按批 1000 循环拉取，limit 生效，万级失败行内存可控；P2#124 收口；1 单测） |
| S2.5.39 | sdk.run 默认值对齐 | ✔ 完成（SDK run require_sample_match 默认 True→False，与 CLI/GUI 一致；P2#125 收口；1 单测） |
| S2.5.40 | fingerprint/content_hash 缓存 | ✔ 完成（CrawlRequest.fingerprint/FetchResult.content_hash 惰性缓存字段，slots dataclass 兼容；P2#66 收口；2 单测） |
| S2.5.41 | 插件加载缓存 + options 隔离 + 实例锁 | ✔ 完成（插件模块按 (path, mtime) 缓存 exec_module；processor_options 键全为已注册插件名时按名分配 options、未点名得空；_processor_instances 加锁消除 check-then-act；P2#76/#79/#80 收口；3 单测） |
| S2.5.42 | StateStore 关闭防护 + rows 白名单 + force 保留 attempts + FK CASCADE | ✔ 完成（close 后 conn 换 _ClosedConnection 占位，访问得受控 RuntimeError；rows() 仅允许 SELECT/WITH/PRAGMA 只读；enqueue(force=True) 不再重置 attempts；record_edits FK 补 ON DELETE CASCADE 声明；claim 原子化并入 S2.5.3；P1#26/P1#44/P3#156 收口；3 单测） |
| S2.5.43 | wait(inflight) 超时 | ✔ 完成（wait 加 timeout（crawl.wait_timeout_seconds 默认 60s），任务挂起不再无限阻塞；P1#35 收口；2 单测） |
| S2.5.44 | 调度租约/并行/时区统一 | ✔ 完成（claim_due 租约可回收；run_due 并行执行（ThreadPoolExecutor + _run_one 拆出，ScheduleStore 加进程内锁 + check_same_thread=False）；allowed_hours 与 next_run_at 统一 UTC 基准；P2#108/#110/#111 收口；2 单测） |
| S2.5.45 | 线程局部 fetcher 关闭 | ✔ 完成（_thread_fetcher 创建的实例登记 _all_fetchers，Pipeline.close 统一回收，消除连接池泄漏；P1#49 收口） |
| S2.5.46 | long_poll 增量落库 | ✔ 完成（S2.5.4 重构已逐条落库；补中途失败保留已收数据测试；P1#57 收口；1 单测） |
| S2.5.47 | InProcessBackend 状态修复 | ✔ 完成（service.run 返回非 dict 时正常置终态不卡 running，worker_main._execute 同步修复；P2#113 收口；2 单测） |
| S2.5.48 | 限速器统一 | ✔ 完成（fetch_many 单/批统一走 self.limiter（to_thread 桥接），删除独立 async_limiter，速率不再翻倍；P2#74 收口；1 单测） |

---

## 1. 源 A P0#1-24（与计划 §6.1 对应）

> 计划 §6.1 的 24 条 P0 是两源 P0 的**合并超集**：A P0#1-24 ⊂ 计划 P0，其中 A 独有项（SSRF/内存耗尽/契约不匹配/XPath 注入/日期时区等）即计划 P0#16-24。本表给出 A 编号 → 计划任务。

| A# | 问题摘要 | 任务 |
|---|---|---|
| 1 | 价格分类正则末尾 `$` 退化为任意匹配 | S1.4.1 |
| 2 | `_walk` 遇 lxml 注释/PI 节点 `.tag` 抛 AttributeError | S1.1.3 |
| 3 | auto-analyze 写 `pagination.type: url_param` 但消费侧只认 `page` | S1.4.5 |
| 4 | `analyze_to_config` 输出与消费侧契约 6 项对不上（"假绿灯"链） | S1.4.5 |
| 5 | `TemplateCombineWorker` 调不存在的 `combine` | S1.4.2 |
| 6 | EasySpider `scrollCount>1` 发 `scroll` 动作崩 | S1.5.7 |
| 7 | template_monitor `None content_type` 抛 TypeError | S1.5.6 |
| 8 | SSE 断开后忙循环占 CPU | S1.4.3 |
| 9 | async 跨事件循环共用客户端 | S1.5.8 |
| 10 | Redis frontier added 恒 0 + 原子性 | S1.5.5 |
| 11 | `run(max_pages)` 就地改写缓存配置，污染后续 | S1.1.1（deep_merge 深拷贝 + 配置不可变） |
| 12 | 归档安全检查 `parts[0]` 无条件读抛 IndexError | S1.3.2 |
| 13 | PDF 共享 DB 多线程事务 | S1.5.1 |
| 14 | commands `__all__` 导出不存在名称 | S1.5.4 |
| 15 | 主页"结果与复核"导航错页 | S3.1.2 |
| 16 | benchmark help 显示字面 `10%%` | S2.1.2 后项 |
| 17 | 字段名插入 XPath 崩溃 | S1.4.4 |
| 18 | lxml.html 独有 API 误用（getpath/text_content/cssselect） | S1.1.3 |
| 19 | DNS 重绑定 SSRF 绕行 + 代理未过 policy | S1.3.5 |
| 20 | 浏览器响应先全量读内存再判大小 | S1.3.6 |
| 21 | headless 引用未定义 `_()` | S1.1.4 |
| 22 | QThread 随窗口销毁崩溃 | S1.1.5 |
| 23 | pyproject 声明 >=3.10 但用 3.11 API | S1.5.3 |
| 24 | pdf_workbench 关闭崩溃 + change_monitor 并发 | S1.1.5 后项 |

---

## 2. 源 B P0#1-15（= 计划 P0#1-15，一一对应）

| B# | 问题摘要 | 计划 P0# | 任务 |
|---|---|---|---|
| 1 | 价格正则 `$` 锚点 | P0#1 | S1.4.1 |
| 2 | deep_merge 浅拷贝污染 DEFAULTS | P0#2 | S1.1.1 |
| 3 | 无文件日志，GUI 日志 100% 丢失 | P0#3 | S1.1.2 |
| 4 | etree.HTMLParser 误用 lxml.html API | P0#4 | S1.1.3 |
| 5 | headless `_()` 未定义 | P0#5 | S1.1.4 |
| 6 | QThread 销毁崩溃 | P0#6 | S1.1.5 |
| 7 | PDF 共享 DB 事务冲突 | P0#7 | S1.5.1 |
| 8 | max_pages=0 被 or 吞 | P0#8 | S1.2.3 |
| 9 | max_pages 只计成功页 | P0#9 | S1.2.4 |
| 10 | 通用异常缺 drain | P0#10 | S1.2.1 |
| 11 | SSE 忙循环 | P0#11 | S1.4.3 |
| 12 | 归档 "." 成员 IndexError | P0#12 | S1.3.2 |
| 13 | 模板合并永远失败 | P0#13 | S1.4.2 |
| 14 | reprocess 构造在 try 外 | P0#14 | S1.2.2 |
| 15 | Pipeline __init__ 不回滚 | P0#15 | S1.5.2 |

---

## 3. 源 A P1#25-110（86 条）

### 3.1 已由现有任务覆盖（40 条）

| A# | 问题摘要 | 任务 |
|---|---|---|
| 27 | safe_regex/value_pattern ReDoS | S4.4.2 |
| 28 | 金额单位首个子串匹配 + 外币 | S2.5.1 |
| 29 | retry 配置不生效 | S2.1.4 |
| 30 | browser 响应整读进内存 | S1.3.6 |
| 31 | headers 广播子资源 | S1.3.4 |
| 33 | request_payload 明文落盘 | S1.3.3 |
| 42 | extractors `json.loads` 无保护 | S1.2.5 |
| 43 | ai_graph 空 choices IndexError | S1.2.5 |
| 48 | crawl4ai 绕过 EgressBroker | S2.5.5 |
| 50 | env_checker 冻结 60s | S3.1.1 |
| 51 | pip 安装冻结 120s | S3.1.1 |
| 52 | 运行历史用当前配置 task_id | S3.1.3 |
| 53 | step3 XPath 注入 | S1.4.4 |
| 56 | PDF 工作台取消卡死 | S3.1.6 |
| 60 | main.py 导航错页 | S3.1.2 |
| 61 | run_preflight 阻塞 UI 线程 | S3.1.1 后项 |
| 63 | log_console `_all_logs` 无限增长 | S3.1.4 |
| 67 | home.py AIEnrich 线程孤儿化 | S1.1.5 后项 |
| 68 | toast 返回 None + 鼠标死区 | S3.1.4 |
| 69 | task_history >100 条误删旧记录 | S3.2.1 |
| 76 | config_serializer passthrough 明文链 | S2.2.2 |
| 78 | reprocess 导出陈旧 | S2.5.2 |
| 81 | ai_env .env 优先级颠倒 | S2.1.3 |
| 82 | deep_merge 污染（重复） | S1.1.1 |
| 83 | validate 失败分支死代码 | S2.1.1 后项 |
| 84 | OCR 多进程无预检 | S2.3.1 |
| 85 | LLM 客户端构造硬失败 | S2.3.2 |
| 86 | Tesseract 语言不兼容 | S2.3.3 |
| 87 | pdfx ingest path+size 跳过 SHA | S4.4 |
| 89 | pdfx 阶段隔离不完整 | S2.3.4 |
| 90 | `_rebuild_wizard` 错对象 | S3.1.2 |
| 91 | 托盘 QMenu 无 parent | S3.1.2 |
| 94 | 无托盘静默 stop | S3.1.5 |
| 102 | i18n 链路失效 | S4.3.2 |
| 103 | prepare_windows_runtime 无信任锚 | S4.3.3 + S4.3.4 |
| 105 | build_windows 无架构断言 | S4.3.4 |
| 108 | spec 无 windowed_traceback | S4.3.3 |
| 109 | value_pattern 编译校验 | S4.4.2 |
| 110 | redis push 计数/原子（重复） | S1.5.5 |

### 3.2 由新增任务覆盖（46 条）

| A# | 问题摘要 | 任务 |
|---|---|---|
| 25 | zip 成员反斜杠 `..\..\evil` 目录穿越 | S1.3.2 后项 |
| 26 | StateStore claim 非原子 + REPLACE/FK + enqueue force + rows SQL | S2.5.42（claim 原子并入 S2.5.3） |
| 32 | sources seed 丢除首个外全部请求 | S2.5.7 |
| 34 | quality_report JSON 裸崩 + SQL 注入面 | S2.5.30 |
| 35 | scheduler finish KeyError + lease 过长 | S2.5.19 |
| 36 | recovery mkdir 同秒冲突 | S2.5.20 |
| 37 | execution_backend chmod 0600 Windows 无效 | S2.5.21 |
| 38 | doctor 探测绕过 EgressBroker | S2.5.22 |
| 39 | workspace 整读多 GB 内存 | S2.5.17 |
| 40 | offline_demo 假 PDF | S2.5.18 |
| 41 | record_sinks fail_open 默认 | S2.5.16 |
| 44 | CookieSession 进程级单例非线程安全 | S2.5.8 |
| 45 | import-easyspider --ir 静默 no-op | S2.5.23 |
| 46 | exporters xlsx 静默 pass | S3.4.1 后项 |
| 47 | plan_compiler Authorization 不脱敏 | S2.2.4 |
| 49 | easyspider wait 写错参数 | S1.5.7 后项 |
| 54 | step3 get_selections 同步假死 | S3.1.20 |
| 55 | pdf_workbench rglob 主线程冻结 | S3.1.19 |
| 57 | pdf_region_selector UI 线程渲染 | S3.1.10 |
| 58 | env_checker 取消对话框静默回退 | S3.1.11 |
| 59 | help_center 未知 id 崩溃 | S3.1.12 |
| 62 | result_table Excel 取消未接线 | S3.1.9 |
| 64 | theme 导航 assert 崩溃 | S3.1.15 |
| 65 | help_dialog tmp 文件泄漏 | S3.1.13 |
| 66 | error_dialog `?` 正则误伤 | S3.1.14 |
| 70 | stealth_settings 绕公共属性 | S3.1.16 |
| 71 | pdf_region page 1 基/0 基不一致 | S3.1.17 |
| 72 | pdf_integration 硬编码对象存储路径 | S2.3.5 后项 |
| 73 | async Retry-After 不封顶 | S2.5.9 |
| 74 | stealth_enhanced 矛盾指纹 | S2.5.29 |
| 75 | resources rglob 无缓存 + audit 无界 | S2.5.27 |
| 77 | markdown_exporter dict evidence 切片崩 | S2.5.28 |
| 79 | auto_pilot 只降不升 | S2.5.26 |
| 80 | redis fingerprint 去重缺失 + seen 不 expire | S2.5.25 |
| 88 | pdfx 类型白名单拒 boolean/entity | S2.3.7 |
| 92 | design_system 缩放被覆盖 | S3.1.22 |
| 93 | async_workers max_rows TypeError | S3.1.21 |
| 95 | browser 配置代理对 Playwright 失效 | S2.5.13 |
| 96 | Selenium 默认不可用 | S2.5.12 |
| 97 | SPA 根节点正则永不匹配 | S2.5.10 |
| 98 | browser 超时后无取消机制 | S2.5.11 |
| 99 | Selenium BiDi 非 PermissionError 挂死 | S2.5.12 |
| 100 | extractors 用户正则灾难性回溯 | S2.5.14 |
| 101 | field_designer 全树 O(n²) | S2.5.14 后项 |
| 104 | add_template_version 硬编码绝对路径 | S4.3.4 |
| 106 | .bat LF/CRLF + PS5.1 乱码 | S4.3.4 |
| 107 | run_windows.bat 行为矛盾 | S4.3.4 |

---

## 4. 源 B P1#16-57（42 条）

### 4.1 已由现有任务覆盖（30 条）

| B# | 问题摘要 | 任务 |
|---|---|---|
| 16 | max_pages 就地改写缓存配置 | S1.1.1 后项 |
| 17 | 密钥前缀少 L | S1.3.8 |
| 18 | .env 优先级相反 | S2.1.3 |
| 19 | 首页快捷入口导航错页 | S3.1.2 |
| 20 | 金额单位首子串匹配 | S2.5.1 |
| 21 | retry 配置不生效 | S2.1.4 |
| 22 | 环境检测冻结 60s | S3.1.1 |
| 23 | completed_with_errors 伪装成功 | S2.4.1 |
| 25 | Redis added 恒 0 | S1.5.5 |
| 26 | 流式模式丢参数 | S2.5.4 |
| 28 | datetime UTC 需 3.11 | S1.5.3 |
| 29 | commands 导出不存在名称 | S1.5.4 |
| 32 | 插件审批门非字面量绕过 | S1.3.7 |
| 36 | _run_stream 漏 summary | S1.2.1 |
| 37 | EasySpider scroll 崩溃 | S1.5.7 |
| 38 | 模板监控 None content_type | S1.5.6 |
| 40 | 运行历史错记录 | S3.1.3 |
| 41 | async 跨 loop | S1.5.8 |
| 42 | CSV 注入前缀绕过 | S1.3.1 |
| 43 | Pipeline close 无异常保护 | S1.5.2 |
| 45 | reprocess 幂等跳过 | S2.5.2 |
| 46 | crawl4ai 绕过 egress | S2.5.5 |
| 47 | PDF 阶段隔离不完整 | S2.3.4 |
| 48 | OCR 语言不兼容 | S2.3.3 |
| 50 | path+size 跳过 SHA | S4.4 |
| 51 | StateStore claim 非原子 | S2.5.3 |
| 52 | LLM 构造硬失败 | S2.3.2 |
| 53 | OCR 多进程无预检 | S2.3.1 |
| 54 | request_payload 明文 | S1.3.3 |
| 55 | headers 广播子资源 | S1.3.4 |

### 4.2 由新增任务覆盖（12 条）

| B# | 问题摘要 | 任务 |
|---|---|---|
| 24 | 资源超限后仍执行 PDF 阶段 | S2.3.4 后项（failed 阶段短路后续） |
| 27 | keyring 无后端未捕获崩溃 | S2.2.1 后项（fallback 兜底） |
| 30 | capability quick 假通过 | S2.1.1 后项（require_features 不静默丢） |
| 31 | 高级可视化 XPath 当 CSS 导入 | S1.4.4 后项（选择器语义统一） |
| 33 | 请求指纹不含 headers | S2.5.5 后项（指纹含 headers 去重） |
| 34 | PDF 工作台取消卡死 | S3.1.6（密钥清理补全） |
| 35 | wait(inflight) 无超时 | S2.5.43 |
| 39 | change_monitor 并发无守卫 | S1.1.5 后项 |
| 44 | shutdown 后 StateStore use-after-close | S2.5.42 |
| 49 | 线程局部 fetcher 从不关闭 | S2.5.45 |
| 56 | file:///placeholder 占位符通过校验 | S1.4.5 后项 |
| 57 | long_poll 收完才落库 | S2.5.46 |

---

## 5. 源 B P2#58-125（68 条）

### 5.1 已由现有任务覆盖（40 条）

| B# | 问题摘要 | 任务 |
|---|---|---|
| 58 | 错误信息中英混杂/挤一行 | S2.1.2 |
| 59 | YAML traceback 直抛 | S2.1.2 |
| 60 | http.engine/ocr_backend 隐藏配置项 | S2.1.1 |
| 61 | 导出格式检查漏 parquet/duckdb | S2.1.1 |
| 62 | ${VAR} 缺失静默空串 | S2.1.2 |
| 64 | 非法日志级别 AttributeError | S1.1.2 后项 |
| 65 | describe_error 未覆盖 urllib/SSL | S2.1.2 后项 |
| 67 | .env 编码/注释/export 前缀 | S2.1.3 后项 |
| 68 | 重试配置双轨 | S2.1.4 |
| 69 | 只解 gzip/deflate | S2.5.6 |
| 70 | 登录失败提示不可达 | S2.1.2 后项 |
| 78 | KeyError 承载消息无候选 | S2.1.2 后项 |
| 81 | responses.csv 无视 outputs 开关 | S3.4.1 后项 |
| 82 | records.jsonl 双写 | S3.4.1 |
| 83 | 全量读内存 + 单条损坏崩导出 | S3.4.1 |
| 84 | CSV 字母排序 + xlsx 无行上限 | S3.4.1 |
| 85 | parquet str() 化 | S3.4.1 |
| 86 | GUI/CLI 双校验器分裂 | S2.1.1 |
| 88 | WorkerTaskRunner 0 条显示成功 | S2.4.1 |
| 90 | 多回退 Path.cwd() | S4.2 |
| 91 | validate_schema 白名单形同虚设 | S2.1.1 |
| 92 | CrawlConfig 与 DEFAULTS 分裂 | S2.1.1 |
| 93 | passthrough 明文快照 | S2.2.2 |
| 97 | LogConsole 无上限 + O(n) | S3.1.4 |
| 98 | pdfx 裸 int()/float() | S1.2.5 |
| 99 | value_pattern 绕过 safe_regex | S4.4.2 |
| 100 | pdfx cli 手写阶段链 | S2.3.4 后项 |
| 101 | pdfx reset 无确认 | S4.3.1 |
| 102 | 外币只认中文名 | S2.5.1 后项 |
| 103 | quality_report score:null 裸崩 | S1.2.5 |
| 104 | copy_zip_member 残留半截文件 | S1.3.2 后项 |
| 107 | ChangeDetector 无护栏抓取 | S3.2.1 |
| 112 | 变更历史无限 + save_rules 非原子 | S3.2.1 后项 |
| 116 | JSONProcessor 裸错无上下文 | S2.5.14 后项 |
| 122 | RedisFrontier.push 计数/去重 | S2.5.25 |
| 147 | _safe_relative 允许纯 "." | S1.3.2 后项 |
| 143 | safe_regex 无 regex 库失保护 | S4.4.2 后项 |
| 145 | ZIP 大小写去重在 Linux 过严 | S1.3.2 后项 |
| 147 | 归档路径安全补全（并入上条） | S1.3.2 后项 |

### 5.2 由新增任务覆盖（28 条）

| B# | 问题摘要 | 任务 |
|---|---|---|
| 63 | load_config root 跨环境漂移 | S4.2 后项 |
| 66 | fingerprint/content_hash 每次重算 | S2.5.40 |
| 71 | cookie 落盘非原子 + 失败静默 | S2.2.3 |
| 72 | cookie 明文落盘 | S2.2.3 |
| 73 | 挑战页/SPA 检测失准 | S2.5.10 |
| 74 | 双限速器互不相通 | S2.5.48 |
| 75 | http.engine 静默回退 urllib | S2.1.1 后项 |
| 76 | build_registry 无缓存 | S2.5.41 |
| 77 | 模块级 import 重量级模块 | S4.1 后项 |
| 79 | _processor 共享 options | S2.5.41 |
| 80 | _processor_instances 无锁 | S2.5.41 |
| 87 | gui/main 顶层三重副作用 | S3.1.7 |
| 89 | WorkerTaskRunner.start 残留配置 | S3.1.8 |
| 94 | AutosaveManager 主线程全量写盘 | S3.1.25 |
| 95 | 配置历史先覆盖后校验 | S3.1.26 |
| 96 | switch_project 不重建组件 | S3.1.27 |
| 105 | 完整性校验不查新增未知文件 | S3.2.3 |
| 106 | 两套归档安全实现 | S3.2.3 |
| 108 | claim_due 租约过期重复领取 | S2.5.44 |
| 109 | AutoPilot 只降不升 | S2.5.26 |
| 110 | run_due 串行阻塞 | S2.5.44 |
| 111 | allowed_hours 时区不一致 | S2.5.44 |
| 113 | InProcessBackend 线程死亡卡 running | S2.5.47 |
| 114 | 列表/嵌套 dict 不参与主题匹配 | S2.5.31 |
| 115 | filter_records 原地污染 | S2.5.31 |
| 117 | TableProcessor 忽略 extract.fields | S2.5.32 |
| 118 | 提取异常被记为 stage=fetch | S2.5.33 |
| 119 | discover_links 不去重不过滤 | S2.5.34 |
| 120 | export 空库静默建库 | S2.5.35 |
| 121 | 异常不发 run_finished | S2.5.36 |
| 123 | 每 URL 全表聚合 | S2.5.37 |
| 124 | retry_failed 一次性全拉 OOM | S2.5.38 |
| 125 | sdk.run 默认值不一致 | S2.5.39 |

---

## 6. 源 B P3#126-156（31 条）

> 均落入 S4.5 P3 批量清理，个别已单独指派。

| B# | 问题摘要 | 任务 |
|---|---|---|
| 126 | canonicalize_url 丢 userinfo | S4.5 |
| 127 | excel_safe 误伤科学计数法 | S3.4.1 后项 + S4.5 |
| 128 | utcnow 秒级精度 | S4.5 |
| 129 | 迁移旧键未清理 | S4.5 |
| 130 | ResponseTooLargeError 被 ValueError 误捕 | S4.5 |
| 131 | jitter 叠加超封顶 | S4.5 |
| 132 | 响应头多值丢失 | S4.5 |
| 133 | retries 语义 + max(1,...) | S2.1.4 后项 + S4.5 |
| 134 | seed=0 被 or 吞 | S4.5 |
| 135 | _emit 每事件重读配置 | S4.5 |
| 136 | choose_processor 调两次 | S4.5 |
| 137 | enrich 无开关 | S4.5 |
| 138 | summary.json 不一致 | S4.5 |
| 139 | config 过滤吃掉 False/0.0 | S4.5 |
| 140 | 异常实例多线程复用 | S4.5 |
| 141 | pool_size 静默截断 8 | S4.5 |
| 142 | bridge_pdfx env 重复读 | S4.5 |
| 144 | 实体表缓存不感知变更 | S4.5 |
| 146 | 压缩比阈值误伤 | S4.5 |
| 148 | portable_data_root lru_cache 热更新失效 | S4.5 |
| 149 | manifest format 不校验 | S4.5 |
| 150 | keyring 文案跨平台错误 | S4.5 |
| 151 | 主题空格拼接假命中 | S2.5.31 后项 |
| 152 | evaluate_topic 重复解析配置 | S4.5 |
| 153 | 评分阈值=基础分 | S4.5 |
| 154 | available 缓存永久 | S4.5 |
| 155 | onnxruntime 全局副作用 | S4.5 |
| 156 | StateStore close 后 AttributeError | S2.5.42 |

---

## 7. 源 A 优化 0-18 / 根因 1-10 / 5 假数据

| 类别 | 条目 | 落地位置 |
|---|---|---|
| 优化 0-18 | 报告第八节"白名单样板/优化项" | §7 质量白名单（`OPTIMIZATION_PLAN_FULL.md`）逐项对齐；pdfx D 系列整改注释/安全修复/最高质量函数作为验收基准 |
| 根因 1-10 | 浅拷贝·主线程阻塞·异常隔离·安全边界·可观测性·配置契约·孤儿代码·包根加载·默认路径·破坏性操作 | §6.4 根因 → 方案 → 阶段表 |
| 5 假数据 | 假绿灯（校验/0条）、假指纹（不含 headers）、假 AI（启发式）、假校验（代理验证/配置 diff）、假语言包 | §6.3 簇表后项：S2.1.1 + S2.4.1 / S2.5.5 / S1.4.5 / S2.1.1 / S4.3.2 |

## 8. 源 B 方案 1-13 / 5 根因

| 方案 | 阶段 | 覆盖任务 |
|---|---|---|
| 1 崩溃三件套（deep_merge+日志+GUI崩溃） | S1 | S1.1.1-5 |
| 2 pipeline 异常隔离 | S1 | S1.2.1-5 |
| 3 GUI 崩溃三件套 | S1 | S1.1.3-5 |
| 4 pipeline 异常隔离 | S1 | S1.2.1-5 |
| 5 正则与字段识别 | S1 | S1.4.1-5 |
| 6 安全边界 | S1 | S1.3.1-8 |
| 7 配置与环境管理 | S2 | S2.1.1-4 + S2.2 |
| 8 PDF 多线程事务 | S1 | S1.5.1 |
| 9 GUI 线程模型 | S3 | S3.1.1-6 |
| 10 日志控制台与内存 | S3 | S3.1.4 |
| 11 配置校验与错误信息 | S2 | S2.1.1-2 |
| 12 安全扫描与自动化测试基线 | S3 | S3.3 + §7 白名单 |
| 13 跨平台适配与一致性 | S4 | S4.2-4.5 |

> 5 根因（源 B）与计划 §6.4 根因 1-10 为同一集合的两视角，映射见 `OPTIMIZATION_PLAN_FULL.md` §6.4。

---

## 9. 新增任务注册（§6.6 引用）

本表所列任务为全覆盖补齐新增，逐一在 `OPTIMIZATION_PLAN_FULL.md` §6.6 有定义。行格式：`任务ID | 位置 | 覆盖源项`。

### S1 阶段
- S1.3.2 后项 | core/archive_security.py + fetching/archives.py | A25、B104/145/147（反斜杠穿越、半截文件、大小写、纯 "."）

### S2 阶段
- S2.2.3 | cookie 落盘原子写 + 加密（Windows 无 chmod） | B71/72
- S2.2.4 | plan_compiler Authorization/Bearer 脱敏 + plan -o 防明文 | A47
- S2.3.7 | pdfx config 类型白名单补 boolean/entity/relationship | A88
- S2.5.7 | sources.seed 保留全部 seed 请求 | A32
- S2.5.8 | CookieSession 单例线程安全 | A44
- S2.5.9 | async Retry-After 封顶 | A73
- S2.5.10 | routing SPA/挑战页检测修正 | A97、B73
- S2.5.11 | browser fetch 超时取消机制 | A98
- S2.5.12 | Selenium guard 默认可用 + BiDi 异常放行 | A96/99
- S2.5.13 | browser 配置代理 context 键修复 | A95
- S2.5.14 | extractors 正则/JSON 容错 + field_designer 上限 | A42/100/101、B116
- S2.5.16 | record_sinks fail_open 改 fail_closed | A41
- S2.5.17 | workspace 流式打包 + 排除旧导出 | A39
- S2.5.18 | offline_demo 合法 PDF | A40
- S2.5.19 | scheduler finish KeyError + lease 缩短 | A35
- S2.5.20 | recovery mkdir 同秒冲突 | A36
- S2.5.21 | execution_backend 会话权限 Windows | A37
- S2.5.22 | doctor 探测走 EgressBroker | A38
- S2.5.23 | import-easyspider --ir 生效 | A45
- S2.5.25 | redis fingerprint 去重 + seen expire | A80/110、B122
- S2.5.26 | AutoPilot 双向调整 | A79、B109
- S2.5.27 | resources rglob 缓存 + audit 裁剪 | A75
- S2.5.28 | markdown_exporter dict evidence | A77
- S2.5.29 | stealth_enhanced 一致性 | A74
- S2.5.30 | quality_report 容错 + SQL 参数化 | A34
- S2.5.31 | 主题匹配递归 + filter 深拷贝 | B114/115/151
- S2.5.32 | TableProcessor extract.fields | B117
- S2.5.33 | 提取异常阶段归类 | B118
- S2.5.34 | discover_links 去重/伪协议过滤 | B119
- S2.5.35 | export 空库检查 | B120
- S2.5.36 | run_finished 事件兜底 | B121
- S2.5.37 | 增量统计替代全表聚合 | B123
- S2.5.38 | retry_failed 分页 | B124
- S2.5.39 | sdk.run 默认值对齐 | B125
- S2.5.40 | fingerprint/content_hash 缓存 | B66
- S2.5.41 | build_registry 缓存 + plugin options 隔离 + 实例锁 | B76/79/80
- S2.5.42 | StateStore 关闭防护（claim 原子并入） | A26、B44/156
- S2.5.43 | wait(inflight) 超时 | B35
- S2.5.44 | 调度租约/并行/时区基准 | B108/110/111
- S2.5.45 | 线程局部 fetcher 关闭 | B49
- S2.5.46 | long_poll 增量落库 | B57
- S2.5.47 | InProcessBackend 状态修复 | B113
- S2.5.48 | 单/批量限速器统一 | B74

### S3 阶段
- S3.1.7 | gui/main 顶层副作用移除 | B87
- S3.1.8 | WorkerTaskRunner.start 残留清理 | B89
- S3.1.9 | result_table Excel 取消接线 + Markdown 后台化 | A62
- S3.1.10 | pdf_region_selector 异步渲染 | A57
- S3.1.11 | env_checker 取消回退提示 | A58
- S3.1.12 | help_center 未知 id 防护 | A59
- S3.1.13 | help_dialog tmp 清理 | A65
- S3.1.14 | error_dialog 正则误伤 | A66
- S3.1.15 | theme 导航常量 | A64
- S3.1.16 | stealth_settings 公共属性 | A70
- S3.1.17 | pdf_region page 基序 | A71
- S3.1.19 | pdf_workbench rglob 后台化 | A55
- S3.1.20 | step3 get_selections 后台化 | A54
- S3.1.21 | async_workers max_rows 参数 | A93
- S3.1.22 | design_system 字体缩放 | A92
- S3.1.25 | AutosaveManager 后台写盘 + 失败提示 | B94
- S3.1.26 | 配置历史恢复先校验 | B95
- S3.1.27 | switch_project 组件重建 | B96
- S3.2.3 | 归档单实现 + 新增未知文件检测 | B105/106

### S4 阶段
- S4.3.4 | 打包/启动脚本簇（信任锚、架构断言、CRLF、bat 行为、路径硬编码） | A103-107
