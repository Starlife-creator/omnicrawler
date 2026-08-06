# 审查报告: tests

## 汇总

- 审查范围：`tests\` 下全部 124 个测试文件（unit / integration / gui 全部子目录），逐行通读断言与 mock 目标，并核对对应 `src\omnicrawl\` 实现。
- 运行验证：
  - 124 个文件 `python -m py_compile` 全部通过（**PY_COMPILE_OK**）。
  - 使用 `.venv\Scripts\python.exe`（Python 3.13.1 + pytest 9.1.1，系统 python 3.13 无 pytest）执行 `pytest tests --collect-only -q`：**696 tests collected in 0.89s，0 errors**。
  - GUI / 浏览器 / 快照类测试因依赖 PyQt6 / Playwright / `OMNICRAWL_BROWSER_TESTS=1` / 基线目录，本环境仅做静态审查。
- 分级计数：critical 0 / high 0 / medium 6 / low 9 / ux 1（另有观察项 4 条）。
- 总体评价：测试体系覆盖面广、质量较高。egress 拒绝路径、安全修复（zip 穿越/符号链接/压缩炸弹、`builtin:` 模板穿越、SHA256SUMS 篡改、归档整体验证后才发布）、恢复状态机（ALLOWED_TRANSITIONS、checkpoint 幂等）、注册表契约（help 8 字段、field spec 7 字段、minimum_catalog 规模）、配置迁移保留未知字段等均有成体系断言。主要问题集中在个别"假阳性断言"、错误的源码路径插入、以及依赖可选包导致整个测试模块被静默跳过。

## 问题清单

### [medium] tests/gui/test_help_button.py:33-34 - 断言存在恒真分支，"32x32 可点击区"验证形同虚设

- 现状：`assert "setFixedSize(32, 32)" in source or "32" in source`。
- 问题：`or "32" in source` 是恒真兜底——源码字符串只要任意位置出现 `"32"`（数字、坐标、注释均可）断言即通过，无法检测 `setFixedSize` 缺失或尺寸退化。当前源码 `src/omnicrawl/gui/widgets/help_tooltip.py:48` 确实有 `setFixedSize(32, 32)`，测试只是碰巧通过，且与第 33 行描述的"至少 32x32 可点击区"意图不符。
- 建议：删除 `or "32" in source`，仅保留 `setFixedSize(32, 32)` 存在性断言（或改为解析 `setFixedSize(\d+, \d+)` 并校验 >= 32）。

### [medium] tests/gui/test_help_button.py:18 与 tests/unit/ai/test_ai_task_designer.py:19 - sys.path 插入路径错误（指向不存在的 tests/src）

- 现状：两个文件均 `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))`。文件分别在 `tests/gui/` 与 `tests/unit/ai/`，`parents[1]` 分别为 `tests/` 与 `tests/unit/`，拼接结果是 `tests/src`、`tests/unit/src`（不存在）。
- 问题：注释声称"从源码树导入 omnicrawl"，实际路径根本不存在；测试之所以能跑，完全依赖 omnicrawl 已安装进 venv。脱离已安装环境（如 CI 只 checkout 源码、或未 pip install）即 ModuleNotFoundError。同文件 test_help_button.py:30 自己的 `parents[2] / "src"` 才是正确写法；正确路径应为 `parents[2]`（gui）与 `parents[3]`（unit/ai）。
- 建议：改正 parents 层级，或直接删除该行（本项目测试普遍直接 `from omnicrawl.*` 导入，依赖已安装包）。

### [medium] tests/gui/visual/test_snapshots.py:18-23 + conftest.py - 视觉回归套件整体处于休眠状态，无人生成基线

- 现状：模块级 `if not BASELINE_DIR.is_dir() and not os.environ.get("OMNI_BASELINE"): pytest.skip(...)`；`tests/gui/visual/baselines/` 目录不存在（已确认）。
- 问题：整个快照回归套件（3 主题 × 组件）在默认环境下永远被跳过，且没有任何 CI/脚本自动生成基线，等于"写了但从未生效"。`OMNI_BASELINE=1` 生成基线后又容易因渲染环境差异产生噪音。
- 建议：提供基线生成脚本并在 CI 固定渲染环境（offscreen + 固定字体/DPR）下先生成基线再启用断言；否则应明确标记为手工维护并给出生成文档。

### [medium] tests/unit/other/test_pdfx_cli.py:10 - 模块级 importorskip("openpyxl") 使整个 CLI 模块测试被可选依赖"绑架"

- 现状：模块顶层 `pytest.importorskip("openpyxl", ...)` 之后再 `from omnicrawl.pdfx import cli`。
- 问题：pdfx CLI 本体并不依赖 openpyxl（仅 XLSX 审查回写用到），缺 openpyxl 时整个 CLI 模块测试（含命令行解析、导出等非 XLSX 路径）全部静默跳过，无法发现 CLI 回归。与 test_gui_smoke.py:184 的做法（`monkeypatch.setitem(sys.modules, "openpyxl", fake)` 注入假模块）相比明显更弱。
- 建议：把 openpyxl 的 skip 下沉到依赖它的具体测试函数（或同样注入 FakeWorkbook），保留其余 CLI 用例常跑。

### [medium] tests/unit/pipeline/test_pipeline.py:40 - 离线集成测试被 openpyxl 可选依赖跳过，核心抓取路径得不到常驻覆盖

- 现状：`PipelineTest.test_offline_crawl_attachment_export_and_incremental` 方法内 `pytest.importorskip("openpyxl")`。
- 问题：该方法是最接近真实端到端的离线集成测试（本地 ThreadingHTTPServer + PDF 附件下载 + 增量），却被导出 Excel 这一步的可选依赖整体跳过；缺 openpyxl 的 CI 上整个流水线集成路径零覆盖。函数级 skip 虽不如 pdfx_cli 严重，但同样"抓取逻辑无错、仅导出缺依赖"时应保留运行。
- 建议：将 XLSX 导出断言部分拆分或注入假 openpyxl，让抓取/增量部分始终执行。

### [medium] tests/gui/test_gui_smoke.py:24,27,33 - 硬编码 UI 结构数量（pageIds==5、模板>=50），回归敏感度差

- 现状：`assert len(window._config_wizard.pageIds()) == 5`（:24、:33）与 `assert len(templates) >= 50`（:27）。
- 问题：向导页数与模板数量是演进性结构，硬编码后新增向导步骤会直接打断测试（可能是意图，但 50 这个魔法数来源不明）；`>= 50` 只防"大量删减"，不防个别模板退化，且与 test_visual_design 等其他用例的模板断言重复。
- 建议：页数改为依据 ConfigWizard 明确定义的步骤常量断言；模板数量改为"包含必需关键模板集合"（如 wordpress、generic 等 id 集合）。

### [low] tests/integration/browser/test_strengthened_features.py:111-115 - 使用真实 datetime.now()，存在整点边界竞态

- 现状：`disallowed = (datetime.now().hour + 1) % 24`，随后断言 `str(datetime.now().hour) in reason`；对比同功能 test_runtime_foundations.py:93-98 已用 `monkeypatch.setattr("omnicrawl.schedule_conditions.datetime", fixed)` 固定时间。
- 问题：`disallowed` 取当前小时，`evaluate_conditions` 内部又是另一次 `datetime.now()`；两次调用若跨过整点（如 09:59:59.9→10:00:00.1），断言可能在极窄窗口失败。`assert allowed is False` 本身恒定成立，但 `in reason` 会随小时翻转失效。
- 建议：复用 test_runtime_foundations 的做法，monkeypatch 固定 `datetime`，消除时间依赖。

### [low] tests/integration/archive/test_runtime_foundations.py:96 - monkeypatch 走废弃别名路径 omnicrawl.schedule_conditions.datetime

- 现状：`monkeypatch.setattr("omnicrawl.schedule_conditions.datetime", fixed)`，而测试本身从规范路径 `omnicrawl.runtime.schedule_conditions` 导入。
- 问题：`omnicrawl.schedule_conditions` 是 `__init__.py` `_DEPRECATED_MODULE_MAP` 注册的兼容别名（依赖 `_setup_compat_aliases` 在 sys.modules 注册同一模块对象）。一旦移除兼容层，该 patch 立即抛 AttributeError。整个测试套件中这是唯一依赖旧别名的用例。
- 建议：改为 patch `omnicrawl.runtime.schedule_conditions.datetime`（规范路径）。

### [low] tests/unit/egress/test_egress.py:28-29 与 tests/unit/egress/test_egress_security.py:28-29 - 真实 DNS 批准地址解析层从未被测试

- 现状：两个文件的 `_Policy.approved_addresses` 恒返回伪造的 `(f"approved:{host}:{port}",)`，test_egress_security.py:20 注释明言 "Stub network policy that skips real DNS resolution"。
- 问题：域名/端口/协议白名单与预算熔断等负向路径覆盖充分，但真正的安全层——`NetworkTargetPolicy.approved_addresses` 的 DNS 解析与批准地址比对——全程未进入执行；若真实实现存在解析缺陷或允许列表绕过，测试无法发现。
- 建议：为真实 policy 的 approved_addresses 增加单测（固定 /etc/hosts 或注入 socket 解析），至少验证"非批准 IP:port 拒绝"。

### [low] tests/unit/plugin/test_production.py:36 - _Response.read 内固定 time.sleep(0.1)，拖慢 robots 相关用例

- 现状：`def read(self, _maximum): time.sleep(0.1); return b"User-agent: *..."`，每个涉及 robots 拉取的断言都白等 100ms。
- 问题：test_desktop_interactions.py:53-58/65-70/81-86 与 test_execution_backend.py:33-35 采用"deadline + sleep(0.01/0.02)"轮询且给足 2-5s 预算（可接受）；此处则是无条件的 100ms 睡眠叠加在纯逻辑测试上，纯浪费。
- 建议：删除该 sleep（调用方不依赖真实耗时），或改由测试显式控制。

### [low] tests/unit/pipeline/test_pipeline_scheduling.py:116,197,225 - 并发重叠测试依赖真实睡眠测量

- 现状：fetch 回调内 `time.sleep(0.02)` 制造并发窗口，断言 `max_active >= 2` 且 `<= concurrency`。
- 问题：依赖调度器恰好在该窗口内派发任务，慢机器/高负载下 `max_active >= 2` 可能偶发失败（偏 flaky）；虽然 0.02s 窗口通常够用，但仍是时序依赖。
- 建议：改为事件/信号同步（如 barrier 或 semaphore 计数），消除时序猜测。

### [low] tests/gui/test_gui_smoke.py:105-134 - 遗留轮询注释矛盾

- 现状：test_toast_automatically_removes_itself 中注释声明"避免固定 400ms 墙钟预算"，实现用 QTest.qWait(25) 轮询 80 次（最坏 2s）。
- 问题：轮询可接受，但上限 80 次 × 25ms 若全不命中则断言失败且无超时提示；同时 `assert overlay._toasts == []` 的失败信息里只打印内部状态。属可接受的韧性设计，仅提示失败诊断信息不直观。
- 建议：超时后断言失败时补充实际等待时长，便于定位事件循环卡顿。

### [ux] tests/integration/template/test_simple_experience.py / test_help_ux.py - 自然语言与帮助搜索断言含中文字符串，需维护中文文案

- 现状：`config.task_description.startswith("每周监测")`、`search_help("翻页 cursor")`、确认文案包含"必须先试跑"等。
- 问题：测试与产品文案强耦合，任一文案微调即红；但也保证了"用户可见文案确实生效"（如快速任务确认必含试跑提醒）。属双刃剑。
- 建议：文案断言收敛到常量/公共文案模块引用，避免散落字符串。

## 观察项（暂不构成问题）

- 测试普遍采用规范导入路径（`omnicrawl.quality.*`、`omnicrawl.sources.sources` 等均为新路径，非旧别名），兼容层依赖仅 1 处（见上）。
- egress 负向路径覆盖非常完整：域名/端口/协议白名单、四维预算熔断（请求/流量/时长/费用）、凭据作用域、凭据外泄拦截、"逐请求安全拦截不可用/默认安全关闭"护栏。
- 安全类回归到位：`safe_extract_archive` 整体校验通过后才发布输出；`SHA256SUMS` 拒绝篡改与穿越且清单为 UTF-8；`safe_object_key` 拒绝路径穿越；`builtin:` 模板引用与穿越拒绝、`%2f` 按字面处理（不误判）；`.env` POSIX 0600；C4A 引擎拒绝私网目标与明文凭据。
- 恢复/可靠性：run 状态机 ALLOWED_TRANSITIONS 白名单、checkpoint 幂等 last-wins、begin_export 幂等、RecoveryCenter 续跑/重试/重新登录、LocalWorker 会话文件含 auth_token 且 AF_PIPE 可重连。
- 注册表/契约类：help 8 字段、field spec 7 字段非空、NON_OBVIOUS_CONTROL_HELP_IDS >= 10、minimum_catalog 各目录规模下限、插件 inspector 结构。
- 配置：迁移保留未知字段与 vendor_extension、diagnostics 默认值（30 天/500 文件/500MB）与非法值拒绝、ai_env 真源+引号转义往返+os.environ 同步、GUI serializer 保留高级段（需 ruamel）。
- 该环境无法验证项：PyQt6 全部交互/主题用例、Playwright 真实浏览器（需 `OMNICRAWL_BROWSER_TESTS=1`）、视觉快照（无基线）、websockets/selenium/psutil/pymupdf 等可选依赖分支。

## 附：规模与执行数据

- 测试文件：124（py_compile 全过）
- collect-only：696 tests collected in 0.89s，0 errors（.venv pytest 9.1.1 / Python 3.13.1）
- 报告路径：`.audit\report_tests.md`
