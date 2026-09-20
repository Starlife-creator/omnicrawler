# Changelog

## Unreleased
## 0.13.1 - 2026-09-20

### 变更

- fix(core): mypy 在 Linux 上判红 `ctypes.WinDLL`；并把 mypy 的分析平台钉成 CI 那一个
- fix(core): Windows 专属的 COM 管道不再拖垮 Linux 覆盖率门禁（逻辑改为全平台可测）
- fix(linux): $VAR 紧跟全角标点必须写 ${VAR}（bash 3.2 会把多字节字符吞进变量名）
- fix(linux): 卸载的数据根检测在 bash 3.2 下静默终止（macOS CI 抓到）
- fix(linux): 就地重跑必须跳过复制而非报错（与方案 §2.1 及文档声明一致）
- feat(windows): I2 首启可选的桌面/开始菜单快捷方式（默认不勾）
- feat(linux): I1 用户级安装（应用菜单条目 + hicolor 图标 + 可干净卸载）
- test(branding): B5 补 —— README 片段引用的文件必须在两仓各自解析得到
- test(branding): B5 跨仓一致性断言（两仓品牌文件逐字节相同、清单双向覆盖）
- branding B3: 主仓门面（41 资产落位 + README lockup + 许可登记 + 入口指引收口）
- branding B2: 二进制图标（4 spec icon= 接线 + 构建产物图标守卫）
- branding B1: 运行时图标接线（窗口/托盘 setIcon + desktopFileName + 9 资产落位）
- feat(extraction): 走查 R5.2 —— 分页信号落成契约形状，iframe 给出定位信息
- feat(extraction): 走查 R3.6 —— 值写在 class 名里的元素能枚举、能取值、能映射
- fix(extraction,core): 走查 R4.3 —— 地址类字段归一为绝对 URL（只补全、不改写）
- feat(extraction,cli): 走查 R4.2 —— JSON 记录路径改候选打分，单对象不再取错
- feat(ai,services): 走查 R4.4 —— AI 的 config_patch 要么接上、要么如实说
- feat(cli,services): 走查 R5.1 —— transform 排序与分组聚合，并撤回过时的「做不到」
- feat(cli,services): 走查 R3.3/R4.1 —— 需求语义有承载、做不到的会说出来
- feat(extraction): 走查 R3.2 —— 交互信号要么变成动作、要么变成明确告警
- feat(extraction,cli): 走查 R3.1 —— auto-analyze 不再无条件用浏览器（保留 --always-browser 逃生阀）
- fix(extraction): 走查 R3.4/R3.5 —— 字段命名纳入祖先类名、恒定列不再被当噪声删掉
- fix(cli,quality,extraction): 走查 R2 —— 让提示指向真因（R2.1–R2.3）
- fix(quality,security,pipeline): 走查 R1 —— 让交付层说实话、默认拒绝状态变更 URL（R1.1–R1.4）

## 0.13.0 - 2026-09-17

### 变更

- feat(tools): 公网原场景受控复测（W5.1 / N1b）—— 三条判据实测全绿
- feat(ci): 离线运行级验收（W3.4）+ 覆盖率基线按 CI 数字收紧（W6.2/W1.3）
- fix(tests): 补 W4.2 漏掉的契约同步 —— release.yml 的 build_python_version 消费者 4→5
- fix(packaging): macOS 包内 tesseract 钉到 @loader_path —— 修「自称自包含却不自包含」（W4.2 第三轮）
- fix(release): 归档冒烟补 Linux 运行前提、并把 runtime-verify 接进来定位 macOS（W4.2 第二轮）
- feat(release): 归档级便携冒烟 job —— 在全新 runner 上把「已上传的归档」再跑一遍（W4.2）
- fix(cli,docs,test): 按作者指南实测出的三处缺口（W3.5）
- fix(packaging): 把 `_ssl` 的 OpenSSL 依赖**钉到 @loader_path**（W4.1 第五轮）
- fix(packaging): 修 macOS 修正脚本的"正确 OpenSSL 从哪来"（W4.1 第四轮）
- fix(packaging): 修 macOS Full 版 `_ssl` 绑定到 cv2 自带 libcrypto 的问题（W4.1 第三轮）
- fix(packaging): 六个便携 spec 排除 nltk —— 修「打了包但 app 起不来」（W4.1 第二轮）
- fix(packaging): 便携 spec 显式声明 ssl/_ssl —— 修 macOS 便携包「启动即崩」（W4.1 抓到）
- ci(release): 手工派发只留 artifacts、不建 Release（W4.1 / 决策 #3）
- fix(gui): 「先记运行归属、再启动」—— 修掉 `start()` 同步到终态时的归属竞态（CI 根因）
- fix(gui): 初始化 `_running_task_id` + 去掉入口用例的运行态竞态（CI 抓到 1 处）
- test(browser): 事件驱动懒加载**定论** —— 平台能力已验收 + 产品动作已知限制（W3.3 / §5.2 #7）
- feat(pdfx): 基准扩三种形态 + 「页码等于真值页」判据（W3.1 / §5.2 #5）
- feat(pipeline): 分页完整性判据 —— 「访问 N 页却只交付 M 条」必须可见（W3.2 / §5.2 #6）
- fix(pipeline): 审计 medium/low 按「影响正确交付」筛选复核，修 5 条（W6.4 / §5.5 #18）
- fix(cli): 两条入口共用启动日志，行为一致（W6.7-④，§5.7 最后一个小项）
- refactor(gui): 运行状态**词表统一** —— 五份表并成一处，partial_success 不再被压平（W6.7 主项）
- refactor(cli,gui,ci): §5.7 三个小项收口（W6.7 ②⑤⑥）
- fix(core): 显式收窄可选依赖返回值 + 新增「缺依赖环境」的 mypy 门禁（CI 抓到 2 处）
- fix(tests): 让既有基准用例造「可比对」——W6.6 契约变更的配套更新（CI 抓到 4 条）
- refactor(gui): `_repolish_widget` 两处重复实现下沉为 design_system.repolish_widget（W6.7-①）
- fix(tests): 字体动态检查改走子进程真实平台，并订正环境表述（本机 full 版）
- fix(tests): 删掉不可移植的字体反向探针（CI 实测：`inFont` 只能证「有」）
- feat(benchmark): 基线记录带「输入快照」+ 四维可比性判定（W6.6 / §5.2 #8 前置）
- chore(licenses): mermaid/echarts 的许可正文随仓分发 + 机器守卫（W6.1 / §5.6 #23）
- fix(gui): 等宽字体补 CJK 回退 + 机检替代人工截图（W6.5 / §5.3 #10）
- chore(mypy): 严格范围扩到 core，并把范围守卫泛化（W6.3）
- fix(gui): 表单同步不得静默丢字段 + W2.2 端到端收官（§5.3 #9 收口）
- feat(gui): 字段表新增「属性 / 取值方式」两列 —— 表单终于能建出「元素自身属性」规则（W2.2 下半 B）
- fix(gui): 选择器是否必填改由「取值位置」契约判定（W2.2 下半 A）
- feat(core): 字段「取值位置」契约 —— 把形状与键名收成单一真源（W2.2 上半）
- docs(coverage): 同步覆盖率阈值 66% → 73%（文档↔代码第二真源守卫抓到的）
- chore(ci): 覆盖率 ratchet 按 CI 跨平台实测收紧（W1.4，原「本地实测 − 8」前提已被推翻）
- fix(ci): Windows 控制台编码**根治**（job 级 `PYTHONIOENCODING=utf-8`）+ 机器契约（W1.3d）
- fix(ci): 门禁打印必须 ASCII 安全（修 Windows 崩溃）+ 浏览器下限按实测校准（W1.3c）
- feat(pdfx): OCR 空白归一按**来源**默认生效，不再要求逐字段声明（W2.1，§5.8 #24）
- fix(ci): coverage json 只取数据、判定交给门禁（W1.3b）
- fix(ci): 覆盖率门禁认环境——浏览器专属下限挪到能达成的 job（W1.3a，**不降标准**）
- fix(runtime): 关闭时回收 worker 子进程——POSIX 僵尸会让「已退出」被误判为资源残留（W1.1b）
- fix(convertx,ci): 缺可选后端时自诊断 + CI 装 storage extra（W1.2）
- fix(runtime): worker 的 UNIX 域套接字改到短路径——深工作区不再起不来（W1.1）
- fix(extraction): 单页模式不再静默丢短值——旧的长度判据同时误伤数据、还留下一个脏值
- feat(pdfx): PDF 侧质量基准——判据「要么对，要么进复核」+ 反例集 + 真 OCR 集成
- test(gui): 修阶段 1 用例的顺序依赖——断言范围收到「本次抓取」
- fix(gui): 接线 `BackgroundWorker.cleanup()`（悬空钩子落地）+ 补 i18n 门禁漏项
- fix(licensing): 补随包字体的 OFL 许可文本与声明——原先只声明了三分之一
- fix(gui): 变更监测关窗后不得留下在飞工作——补取消/等待入口 + 关闭后不再启动检查
- fix(packaging): install_windows.ps1 补 Python 版本闸门 + 复核 audit-20260805 余下 9 份报告
- feat(pagination): 分页形状收成唯一契约 + 表单可 author 分页（含游标）
- feat(gui): 表单可选「数据来源」——从零在表单里建出 API 任务
- feat(release): 产物依赖与锁的严格对账——wheel 的 Requires-Dist ↔ uv.lock 逐条比对
- feat(benchmark): 质量基准扩到 4 个任务 / 3 种取数形态（N1a 覆盖面露口）
- feat(gui): 表单接上产品自带 DOM 分析器——「分析页面并填字段」
- feat(gui): 表单可创建「列表」任务——补上列表项选择器入口，闭环上一轮登记的能力缺口
- test(gui): 补「经 GUI 表单创建任务」证据；顺带暴露一处能力缺口
- fix(extraction): 自动配置不再丢「链接外字段」，也不再拿侧边栏当列表（N1c 两项）
- refactor(fetching)+chore(lint): 导入块内不再夹 LOGGER；E402 预算 59 → 45
- feat(pdfx)+docs: `--config` 位置自由；走查文档样本数字纳入门禁校验
- fix(pdfx): 数值 token 改「结构判定」——OCR 千分位误读不再被静默截断成错值
- test(browser)+refactor(fetching): 补「滚动加载」固定样例；浏览器启动参数收口为单处真源
- test(browser): 补「动态页面→分页→去重」固定样例（N2 最后一条部分验证收官）
- feat(tools)+docs: 走查资产跨机可用（按平台隔离、缺依赖降级、接入既有安装路径）
- feat(pdfx): OCR 空白归一做成字段级显式开关（默认关，语言感知）
- feat(tools): 人工走查做成可复用资产（固定样本 + 隔离工作区 + 可复制记录表）
- fix(cli): 让 `omnicrawler pdf --config X <stage>` 真正可达（自定义 PDF 项目）
- test(pdf): 补「附件→PDF/OCR→复核」端到端（自动部分）；账本拆成自动/人工两条
- feat(quality): 首轮同步标记为「基线」并把提示决定权交给消费方（用户拍板方案 C）
- docs(compliance)+test: 按实际实现修正 INV-008 声明，并补齐 INV-004/INV-008 的真实证据
- fix(gui): 取消不再被呈现为「错误」——独立终态 cancelled（用户拍板方案 A）
- test(isolation): 真实入口用例改用 monkeypatch 打补丁，避免类属性泄漏到同进程后续用例
- test(integration): 补 GUI 真实入口用例——点「运行」按钮 → 子进程 → 结果页可见
- test(integration): 补「长任务→GUI 停止→重启恢复」端到端；登记"取消被呈现为错误"
- test(integration): 变更工作流补「差异导出」端到端；更正账本中"导出产物未见"的误判
- fix(quality): 变更识别认中文字段键，并补变更检测端到端证据（含 INV-008 缺口登记）
- docs(ledger): API 工作流升级为含 GUI 运行路径已验证；登记 JSON 模式校验缺陷（已修 4a546ec）
- fix(gui): 校验按抽取模式区分——合法 JSON 配置不再被「选择器必填」挡在门外
- test(integration): GUI 闭环补两条——列表→详情两级抓取 + XLSX 可重新打开
- test(integration): 补 GUI→真实子进程→本地站点→结果可见→进程退出 的闭环证据
- docs(ledger): 登记 GUI 配置往返静默降级（已修 a5bdfbd）与 GUI 闭环证据缺口
- fix(gui): 配置往返不再用硬编码默认值覆盖透传键——修复 GUI 运行静默降级
- build: 让便携产物消费锁定依赖
- fix(pipeline): 重建增量游标分页链
- fix(extraction): 真实校验 JSON 自动配置
- fix(fetching): 超时DOM恢复不再伪造成功状态
- fix(pipeline): 避免重定向目标重复交付
- feat(quality): 收紧任务交付验收判据
- fix(pipeline,sources): 分页不再用"点击下一页"动作；浏览器源可翻页且子请求继承渲染
- fix(fetching): 等待条件超时不再当作抓取失败——改用已加载 DOM 继续
- fix(security): apex 与 www 视为同一站点，redirect 不再被判「超出种子站点」
- fix(extraction,templates): 真实用户场景测试驱动的缺陷修复
- chore: `.audit-tmp/` 纳入 .gitignore（与 `.test-tmp/` 同性质的临时目录）
- fix(gui): audit-20260805 逐条复核完成——45 个标题全部给出结论，21 条「仍存在」全部修掉
- chore(mypy): 严格范围扩大到整个 gui 包——量化门禁最后一项未启动项收口
- feat(market): 离线可用纳入机器验证——把「不适用」与「通过」分开
- feat(benchmark): 数据完整性与准确性基准——补上「可复现任务基准」的质量侧
- feat(market): 内容质量可机器验证——补上信任档位输入的生产者（本项目第 5 例「定义了没有生产者」）
- perf(gui): 三个未接线 worker 逐项测量后定论——一个接线、两个判定不接线
- refactor(cli): P2-4 入口收敛——pdf 提为一等子命令、镜像补齐 3 个入口、工作台入口统一
- ci(deps): P1-2 uv.lock 接入 CI——补齐「按锁文件可复现」的两条验收
- fix(security): P2-5 TLS 校验降级的作用域收紧——关掉校验不再等于对所有目标降级
- fix(reliability): 取消不应把待抓页面判成终态 blocked——否则 resume 静默丢页面
- chore(maintainability): 门禁清单唯一化 + 自证入口唯一化（主线第 4 步·三件套）
- fix(reliability): 取消真实有效 + 中断可恢复——补齐可靠性闭环（修三处真实缺陷）
- fix(reliability): 异常不再伪装成功——填上早已定义却从未发出的 partial_success
- fix(i18n-gate): 门禁真正跟踪三引号串——修 docstring 续行误报（并补门禁自测）
- fix(benchmark): 可复现基准与公平对比——档位真生效、指标不再恒 0、基线只取可用运行
- feat(gui): 三态统一（错误不伪装成没数据）——主线第 3 步补完
- feat(a11y): 存量收敛至零 —— 16 文件补 18 处无障碍名（主线第 3 步·收官）
- feat(a11y+style): 内联样式改设计令牌 + 补 a11y（主线第 3 步·第三批）
- feat(a11y): 高频入口页面补无障碍名（主线第 3 步·第二批）
- feat(a11y): 共享组件补无障碍名（主线第 3 步·第一批）
- docs(journey): 首个任务旅程人工走查清单（把「双证据」的分工变成可执行步骤）
- test(journey): 首个任务旅程自动回归（主线第 2 步）
- feat(gui): 页面骨架 BaseView + 首例迁移示范（主线第 1 步·B）
- feat(gui): 设计体系继承门禁（主线第 1 步·A）——新页面零容忍、存量只降不升
- feat(quality): P2-1 覆盖率 ratchet（按包下限 + P1-3 模块族纳入门禁）
- feat(quality): P2-2 ruff 豁免预算门禁（存量违规只降不升）
- chore(arch): 重设 cycle budget 为实测值（三项大幅收紧、components 按打散后重设）
- refactor(arch): 版本号下沉叶子模块 + driver 结构协议，导入环 62 → 11 模块
- fix(tests): 拆库演练拷贝忽略缓存目录（修 test_standalone_copy_passes_check）
- fix(convertx): 显式点名导出 + 修正测试 monkeypatch 注入点（P0-2 遗留，5 个测试转绿）
- fix(convertx): 补 _ordered_columns 到包级兼容垫片（P0-2 遗留，使 tests/unit 可收集）
- fix(gui): 补 pdf_workbench 的 _collect_failures 再导出（上一批遗漏）
- refactor(state): P1-3（state_store.py）第三批——运行/导出/产物域外迁，文件收官
- refactor(state): P1-3（state_store.py）第二批——记录域与质量域外迁
- refactor(state): P1-3（state_store.py）第一批——队列域与插件状态域外迁
- refactor(fetching): P1-3（browser_fetcher.py）第三批——Playwright 池化层外迁
- refactor(fetching): P1-3（browser_fetcher.py）第二批——失败关闭守卫外迁
- refactor(fetching): P1-3（browser_fetcher.py）第一批——引擎适配器层外迁
- refactor(plugins): P1-3（plugin_broker.py）第六批——宿主注入能力域 Mixin，文件收官
- refactor(plugins): P1-3（plugin_broker.py）第五批——fs + network 能力域 Mixin
- refactor(plugins): P1-3（plugin_broker.py）第四批——artifacts 能力域 Mixin
- refactor(plugins): P1-3（plugin_broker.py）第三批——records 能力域 Mixin
- refactor(plugins): P1-3（plugin_broker.py）第二批——IPC 循环驱动外迁
- refactor(plugins): P1-3（plugin_broker.py）第一批——能力契约叶子模块外迁
- refactor(gui): P1-3（pdf_workbench.py）第三批——结果与收尾域 Mixin，文件收官
- refactor(gui): P1-3（pdf_workbench.py）第二批——拖放扫描域 Mixin
- refactor(gui): P1-3（pdf_workbench.py）第一批——流水线 Worker + 纯逻辑外迁
- refactor(gui): P1-3（plugin_market.py）第六批——目录加载域 Mixin，文件收官
- refactor(gui): P1-3（plugin_market.py）第五批——安装流程域 Mixin
- refactor(gui): P1-3（plugin_market.py）第四批——浏览域 Mixin
- refactor(gui): P1-3（plugin_market.py）第三批——插件动作域 Mixin
- refactor(gui): P1-3（plugin_market.py）第二批——3 个后台 Worker 外迁
- refactor(gui): P1-3（plugin_market.py）第一批——纯逻辑块外迁 plugin_market_logic.py
- refactor(plugins): P1-3（plugins.py）第四批——加载器域外迁，plugins.py 收敛为纯门面
- refactor(plugins): P1-3（plugins.py）第三批——Registry 与 Factory 类型别名外迁
- refactor(plugins): P1-3（plugins.py）第二批——插件契约叶子模块 + 静态预检域外迁
- chore: 救援重建——以远端 main 为基线，整合 2026-09-10 全部优化成果
- P1-1 main.py 委托聚合：2240 → 2113 行（-127 行），删除 38 个委托转发桩、40+ 处调用点直连到 delegates 域协调者，6 个改动文件 AST 校验全部通过，tests 对所有被删名零引用（保留 _apply_ui_mode/_set_theme/_show_error_dialog 等有测试或动态检查依赖的兼容转发）。 P1-2 运行时依赖锁：uv.lock 已生成并落盘——226 个包、约 1.1MB，含 Windows/macOS/Linux × py3.12/3.13 多平台解析标记。
- Refactor convertx into _core; modernize GUI workers
- docs: rebuild documentation index and enforce it in CI
- docs: close conditional review queue assessment
- fix(gui): clear stale resource readings
- refactor(gui): isolate plugin activation service
- fix(gui): include child process memory in monitor
- test: track extended convertx benchmark cases
- docs: update optimization implementation status
- perf(convertx): stream jsonl to xlsx
- perf(convertx): stream jsonl to duckdb
- perf(convertx): stream jsonl to parquet
- perf(convertx): bound jsonl to csv memory
- fix(convertx): preserve late parquet fields
- perf(convertx): stream duckdb reads to jsonl
- perf(convertx): stream parquet reads to jsonl
- perf(convertx): stream xlsx reads to jsonl
- perf(convertx): reduce xlsx writer memory
- perf(convertx): bound automatic csv decoding memory
- perf(convertx): stream jsonl output paths
- perf(convertx): stream csv to jsonl conversion
- perf(convertx): add isolated memory benchmark
- ci: pin market snapshot and add manual compatibility checks
- fix(convertx): report data loss and safely cancel conversions


### 变更

- fix(extraction): 单页模式（详情页）不再静默丢短值——旧实现用一条 `len(text) < 5` 同时挡 UI 装饰又误伤数据：4 字标题被丢（标题字段整个没有）、`9`/`元` 被丢而父节点合并文本 `'9
    元'` 当了「价格」（脏值）、同名不同选择器的字段被名字去重挤掉。现为：长度规则＝**必须有可取值字符**（数字/字母/汉字）、装饰由**区域**判据挡（aside/nav/footer）、去重键含 CSS 路径、容器（文本＝直接子元素拼接）跳过取叶子
- feat(pdfx): 新增 PDF 侧质量基准 `services/pdf_quality_benchmark.py`（数字版 + 图片版两用例，样本程序自造、离线、可复现；与 crawler 侧**分开记分**）。判据＝**要么对，要么进复核**：归一后逐字比对 + 每字段带页码与原文证据 + 低置信（`validation.auto_accept_confidence`）必须 `needs_review` + 错值不得被 `auto_accepted` 放行；11 条反例单测 + 3 条真跑集成用例（含真 OCR，缺字体或 tesseract 时跳过并登记）
- fix(gui): 变更监测在关窗后不再留下在飞工作——补 `ChangeDetector.cancel()` 与 `ChangeMonitorView.shutdown()`（先停 30s 轮询、再取消并有界等待），关闭流程后再触发轮询/手动检查都不再启动新工作；旧 worker 的迟到终态信号被忽略
- fix(gui): 接线 `BackgroundWorker.cleanup()`——该钩子自仓库初始提交起就存在却**从未被调用**（全历史无 `self.cleanup()`）；现在成功/失败/取消三条路径都会在工作线程内执行，晚于终态信号，且清理异常不会改变任务终态
- fix(licensing): 为随仓（且随便携包）分发的 6 个字体补 `_shared/fonts/OFL.txt`（逐字上游文本 + 三家版权行），并在 `THIRD_PARTY_NOTICES.md` 补声明 JetBrains Mono 与 Outfit；新增守卫按字体 `name` 表核对声明与版权行
- fix(packaging): `install_windows.ps1` 在装依赖前校验解释器版本（>=3.12，取值来自 `pyproject.requires-python`）——此前 `py -3` 没有版本上界，可能选到 3.10/3.11，venv 建得起来但依赖装不上，报错点离原因很远；同时修掉过时提示语「Python 3.10 or newer」
- feat(pagination): 分页形状收成唯一契约 `src/omnicrawler/core/pagination.py`（核心校验与 GUI 共用）；游标配置缺 `next_path` 从「静默只采一批」改为**加载时即报错**
- feat(gui): 「高级设置 → 分页方式」支持按页码/偏移与按游标/下一页值，并保留 `location` 等表单不渲染的分页键 ⇒ 从零在表单里可建分页任务（含游标），无需手写 YAML

## 0.12.0 - 2026-09-02

### 变更

插件平台、安全边界与桌面体验全面升级：新增可审核的声明式资源视图和宿主管理背景层，完善运行类型、项目级精确授权、启用/禁用热加载及创作者签名确定性；同时强化异步网络隔离、PDF 管线、任务创建体验、架构与 SDK 契约，并将跨平台发布流程拆分为可复用且带产物预算、SBOM、来源证明和签名证明的构建门禁。


### 变更

- refactor(architecture): 拆除 core→pdfx 与 services→gui 反向依赖，并加入依赖方向和循环复杂度预算门禁
- feat(pipeline): 增加 `PipelineDependencies` 显式资源注入，支持由宿主控制状态库、对象存储和记录 sink 生命周期
- feat(contracts): 将对象存储、记录 sink 与 OCR 后端固化为可运行时检查的 Protocol，并补跨实现契约测试
- ci(deps): 增加最小安装与分 feature extras 的隔离安装验证，防止可选依赖泄漏到核心启动路径
- ci(sdk): 增加 SDK 公共导出快照，阻止未声明的破坏性 API 漂移
- ci(release): 增加跨平台 Standard/Full 产物预算和 Standard 重型依赖污染检查
- feat(gui): 增加宿主管理的语义底层视觉表面、透明前景预设和无需插件的本地工作台背景
- feat(plugins): `surface.background` v2 支持范围、面板不透明度、遮罩、静态模糊、暂停与能力发现，同时保持输入穿透和宿主渲染边界
- fix(plugins): 市场插件启用后立即按当前项目授权热加载，并修复 Windows 完整包安装目录继承临时 ACL 的问题

## 0.11.2 - 2026-08-30

### 变更

市场生态安全加固、创作者私下分享与正式发布体验完善


### 变更

- fix(state): 恢复 StateStore.review_queue 类方法结构，修正 SQL LIKE 后缀漏匹配并补 run/limit 回归测试
- fix(plugins): 完整签名包拒绝被旧式单文件安装覆盖，修复市场分发代码的 mypy 回归
- ci(release): 增加发布前快速门禁与 tag/版本校验，手动构建不再误创建 Release
- ci(release): 修复 Windows Chromium/OCR 缓存回填，启用 pip 缓存并稳定大型资产缓存 key
- ci(release): Windows、Linux、macOS 六个便携包统一生成 keyless attestation
- test(deps): 固化 Full/full-macos 依赖并集与平台差异契约
- feat: complete creator sharing and market update UX
- feat: establish signed plugin distribution workflow

## 0.11.1 - 2026-08-26

### 变更

- fix: v0.11.1 双红修复——UA 脆性子串断言 + macOS BSD stat 不兼容
- fix(release): 资产挂载循环显式 GH_REPO——进入产物目录后仍能定位仓库

## 0.11.0 - 2026-08-26

### 变更

- ci(release): 发布闸门三层漏斗——单包超限不再劫持整次发布
- fix: 补提交三处修复——修 CI quality 红（release_integrity 静态导入门禁）
- test: 白名单防漂移守卫指向 catalog_lib/common.py（拆分后的单一事实源）
- chore(security): 透明日志诚实降级为 informational-only（长期债 #5 方案 B）
- refactor(gui): 抽离 MainWindow 三个内联对话框至 views/project_dialogs（长期债 #1 Phase A）
- refactor(cli): build_parser 335 行单体函数按域拆分至 _parsers/ 包
- style: 移除 delegates 测试中未用的 RunController 导入（services 同名类为准）
- test: 补 CLI 注册表契约与 delegates 布线测试；收编 tests/unit/other 杂物桶
- fix: 路线图第三梯队低成本项落地（R-1/R-2/D-3/D-8/S-1 口径）
- fix: 审查 FINAL 路线图梯队一/二 13 项落地（GUI 生命周期 + 安全卫生 + 数据正确性）
- chore(deps): 根生态也忽略 PyYAML——PR #53 来自 / 生态 runtime 组，补齐 #52 未覆盖面
- chore(deps): dependabot 忽略 PyYAML——paddlex 精确锁 6.0.2，升级必撞 PIP_CONSTRAINT
- chore(deps): bump ruff from 0.16.3 to 0.16.4 in /constraints (#46)
- chore(deps): bump mypy from 2.3.0 to 2.3.1 in /constraints
- chore(deps): update reportlab requirement
- chore(ci): bump actions/checkout from 4.2.2 to 7.0.1
- fix(types): mypy 修复——CI quality 门禁 mypy 步骤全绿（存量 16 + 本 PR 9 处）
- style: ruff 修复——导入排序（main.py/change_monitor.py）+ 移除未用 logging
- fix(ci): license-gate 修复 pip licenses 子命令调用——pip 子命令插件已废弃
- fix(deps/tests): 修复 main 存量 CI 失败——pypdfium2 约束对齐 5.x + 测试适配
- fix(gui/core): 项目审查 P0-P2——安全加固、设计令牌统一、冷启动提速、GUI 单测
- feat(plugins/gui): v22 收尾——沙箱资源上限、AI sidecar 接线、自动导出 Markdown
- feat(plugins): Phase 3 B2——plugins list 契约形态列 + PluginMetadata.contract_shape
- feat(plugins): Phase 3 Q4/G3——审核辅助分析（AI 增强审核员，纯静态证据非门禁）
- docs(plugins): Phase 3——契约 2 双契约文档 + 作者指南 + 审核清单
- feat(plugins): Phase 3——scaffold-contract2 脚手架 + inspector 双契约识别 + CLI 接入
- fix(test): 去除 test_phase2b_broker 重复函数定义（F811）
- test(plugins): Phase 2b 配额经会话透传 E2E——契约 2 插件触发 E_QUOTA
- feat(plugins): Phase 2b H4——共现事件 SIEM 导出 + 企业模板预置 + CLI --export-egress
- style(test): phase2b loader 测试 ruff 修复（未用变量/import 排序）
- feat(plugins): Phase 2b loader 接线——daily_quota/egress_policy 配置解析生效
- feat(plugins): Phase 2b N3 keepalive 长驻会话池——跨 run 复用 + idle 回收 + hook 分发
- feat(plugins): Phase 2b broker 层三项——daily 配额 / egress_policy 共现 / files 逃逸拒绝
- feat(plugins): Phase 2a H1 安全指标全集 + H4 环境诊断报告（plugins audit --report）
- feat(plugins): Phase 2a G1/G2/G3 客户端 catalog 信任链（验签+哈希+吊销+防重放）
- feat(plugins): Phase 2a D4 沙箱内纵深防御——脱敏/配额/AST 门（OS 层第二道防线）
- feat(plugins): Phase 2a D2/D3 OS 沙箱抽象层——能力探测 + fail-closed 裁决
- feat(plugins): Phase 2a C6 审计留痕 + O 密钥零暴露（secrets.get 例外）
- feat(plugins): Phase 2a 门 1/门 3 落地——声明一致性 + dependencies 双向互证
- feat(ci): Phase 2a F3 插件沙箱 CI 矩阵 workflow（契约/穿透 + AC spike）
- test(plugins): Phase 2a F2 差分等价测试——能力代理不改插件语义
- feat(plugins): Phase 2a F1 公共契约测试夹具（本地绿 = CI 绿）
- feat(plugins): Phase 2a B5 audit 扩展——契约形态一致性 + 沙箱可用性探测
- feat(plugins): Phase 2a B4 加载器路由接线——契约 2 subprocess 插件真正走沙箱
- feat(plugins): Phase 2a B4 契约形态静态检测（加载器路由分流依据）
- feat(plugins): Phase 2a B4 集成层——子进程组件适配器（pipeline ↔ 契约 2 会话桥接）
- feat(plugins): Phase 2a B4 路由矩阵裁决器 + B5 配置键（runtime_backend 三态）
- feat(plugins): Phase 2a C2 验签接线 V2 + C3 能力代理 + C4 IPC 循环（C2/C3/C4/N）
- feat(plugins): Phase 2a C1 双后端隔离启动 + session 生命周期（C1a）+ IPC v1（C4）
- feat(license): Phase 0 验收——宿主许可 AGPL-3.0 → Apache-2.0（第 81 轮用户决策）
- refactor(gui): Phase 0 M0b——PyQt6(GPL) → PySide6(LGPL) 全量迁移，许可自由完成
- fix(deps): 依赖探测点全面去 PyMuPDF 化（Phase 0 联动盲区清扫）
- fix(capabilities): pdf 特性探测 fitz → pdfplumber（Phase 0 依赖替换收尾）
- test(pdf): 测试 fixture 全面去 fitz 化——PyMuPDF 从依赖树彻底移除（Phase 0 许可收尾）
- refactor(pdf): Phase 0 M0a——PyMuPDF(fitz) → pypdfium2/pdfplumber/pypdf/reportlab 分治替换
- feat(market): 离线快照同步（Phase 1 schema）+ tombstone 测试
- feat(schema): Phase 1 B1 schema 扩展主仓接线——execution_mode/dependencies/input_files
- feat(plugins): Phase 1 许可治理——license 必填 + 门 2 SPDX 白名单 + license-gate + 本地 audit
- docs: 发布流水线故障排查手册——v0.9.1 全轮次复盘（40+ 轮/90+ 提交）

## 0.9.1 - 2026-08-16

### 变更

构建链优化：跨平台对称性（M1-M4）+ 阶段A健全性（P2/P3/P8/P10/P11/S6）+ 阶段B Linux真Full/macOS弱Full 构建链（Full spec/运行时制备/冒烟）+ full-macos extras

## 0.9.0 - 2026-08-16

### 变更

0.9.0：全仓安全审查修复落地（8 阶段：公式注入/路径穿越/ReDoS/信任链/AI 隐私/源类型白名单/供应链硬化/文档测试收口）+ AI 外发隐私闸门 fail-closed 全落点接线 + 插件注册源校验 + 市场仓 D2-A 独立模板 id + B02-023 重签 + CI 三平台门禁全绿


### 变更

T 批次：场景体系闭环——SceneStore 新增已验收候选文档级透视（accepted_values）、candidates 补 document 维度；ScenePanel 新增「导出已验收结果」（JSON/CSV，标准库零依赖）与「生成为任务字段」（槽位→extract.fields YAML + 复制剪贴板）；document_ir HTML 正文主体抽取（正文容器词典 main/article/#content/.entry-content 等，未命中回退全页，main_content 选项默认开）；normalizers L3 槽位注释澄清（设计预留，LLM 修复走 shadow_repair）。

## 0.8.0 - 2026-08-12

### 变更

0.8.0：新增离线市场快照与3套官方模板（Crossref著作/GitHub公开Issue/OpenAlex著作，含签名与说明）及模板发布校验工具链；GUI本地任务进度条接管Worker日志增量解析，新增开发者检查器视图并接入导航；模板/插件市场支持离线bundled_catalog加载；增强core配置解析健壮性与worker进度数值安全；修复quality CI 5项失败（路径解析/测试桩签名/ruff排序/mypy类型），优化Windows构建去重与依赖约束。

## 0.7.0 - 2026-08-11

### 变更

0.7.0: trustchain hardening, guardrails, i18n, metadata

## 0.6.0 - 2026-08-10

### 变更

feat(extraction): L3 自适应提取闭环（失效检测→LLM 重生成→本地验证）；Markdown 降维与语义分块；SimHash 双层去重（URL 规范化 + 内容指纹）；curl_cffi TLS 指纹伪装（含 DNS 钉扎与出口审计，未装时回退 httpx）；新增 tls extra；签名密钥默认路径改为主目录相对；市场拆分为独立仓库（git-as-registry）；CI 双库 checkout；市场测试缺失时自动跳过


### 变更

- chore(quality): bump pip 24.3.1→26.1.2, pytest 8.4.2→9.0.3 (PYSEC-2026-196/1795/1796/2875/2876, PYSEC-2026-1845); cryptography <50→<51 (PYSEC-2026-3552, fix 50.0.0)
- ci(test): run GUI tests in the full matrix (gui extra + QT_QPA_PLATFORM=offscreen); coverage 51%→70%, gate restored to 66% (desktop_core 65%)

## 0.5.0 - 2026-08-08

### 变更

- docs: CONTRIBUTING 增加插件提交与签名流程
- feat(plugins): ed25519 离线插件签名验签 (C9 安全)
- chore: ignore stray tmp_es.json test artifact
- chore: stop tracking stray tmp_es.json test artifact
- chore(quality): close acceptance gates - mypy clean (240 files), network boundary approved, coverage gates pass
- chore: remove stray tmp_es.json test artifact from repository
- feat(runtime): track omnicrawler.runtime module (was git-ignored by runtime/)
- fix(build,portable): audit fixes Phases 2/5c — first-run UX, packaging tooling
- fix(gui,pdf): audit fixes Phases 3/5a — PDF extraction accuracy, cross-platform GUI
- fix(core,ai,cli): audit fixes Phases 0/1/5b — config single source, AI pipeline, CLI robustness
- Sync CWD; validate Tesseract downloads
- Add artifacts README; update gitignore and docs
- Enhance Windows packaging and versioning

## 0.4.0 - 2026-08-07

### 变更

release 0.4.0：优化计划阶段 0-4 全部完成（基线 1114 测试通过）。阶段 2：状态机/重试/异常隔离/流式传输/安全边界/调度/导出/工作区打包等 48 项；阶段 3：GUI 线程模型/孤儿接线/CLI 契约/配置往返/导出器；阶段 4：包根惰性化/默认路径/破坏性命令防护/i18n 修复/打包脚本/P3 批量清理（126-156 全收口）。

## 0.3.0 - 2026-08-05

### 变更

- feat(runtime): 补跟踪 omnicrawler.runtime 模块（此前被 .gitignore 误忽略）
- fix(build,portable): 审计修复 Phase 2/5c — 构建脚本、运行时准备、工具脚本、便携首启体验（F1-F55）
- fix(gui,pdf): 审计修复 Phase 3/5a — PDF 抽取准确度、跨平台 GUI 稳健性（D8-D66、A13-B16）
- fix(core,ai,cli): 审计修复 Phase 0/1/5b — 配置单一真源、AI 链路端到端、CLI 管线（C1-C50、E3-E16）

## 0.2.0 - 2026-08-04

### 变更

- release: bump to 0.1.0
- release: bump to 2.8.0
- Initial commit

## 0.1.0 - 2026-08-04

### 变更

回滚测试

## 0.1.1 - 2026-08-04

### 变更

- release: bump to 0.1.0
- release: bump to 2.8.0
- Initial commit

## 0.1.0 - 2026-08-03

### 变更

- release: bump to 2.8.0
- Initial commit

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
- Standard/Full 构建新增独立 `omnicrawler-worker.exe`、运行时完整性清单和 Edition 分层冒烟；Windows
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
- 修复冻结应用找不到 `omnicrawler` 命令和本地帮助文档的问题；GUI 与 CLI 共享应用
  本地配置、日志、断点、模型与结果路径。
- 简单、专业、开发者三种模式渐进展示，切换模式不会删除未知字段或高级配置。
- 新增 GUI“运行能力与自包含组件”页面以及 `omnicrawler capabilities --verify-imports`。

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
