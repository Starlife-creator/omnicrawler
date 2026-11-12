# OmniCrawler 测试报告

> 适用版本：1.0.1 · 配置协议：v5 · 维护状态：现行

> 当前版本：1.0.1。以下保留 2.1.0 与 2.6.0 的历史基线；当前本地 E2E 结果见同目录 `E2E_TEST_REPORT.md`，全项目回归基线见文末更新。

## 2026-11-12 三平台首个全绿 run 的最后一项：一条「防卡死」上界写死了秒数

`03b1c9d5` 之后 `gui-and-browser` 已 success、ubuntu 与 macos 两条 `test` 腿也 success，**只剩 windows 腿 `1 failed`**：

```
FAILED tests/unit/runtime/test_scheduler_parallel.py::test_run_due_runs_in_parallel
  - AssertionError: run_due 耗时异常: 3.98s
1 failed, 6035 passed, 75 skipped, 1 warning in 620.01s (0:10:20)
All coverage gates passed.
```

**它不是并行性回归**。该用例声称的判据是 `:40-47` 的三区间两两相交（`assert overlapped`，与机器速度无关），那条**通过了**；判红的只是紧随其后的「宽松上界」`assert elapsed < 3.0`（原 `:49-50`）。原注释写的是「仅用于抓整体卡死；即便 CI 有 6~10 倍倍率也不误报」，但串行下界是 3×0.2s=0.6s，Windows runner 实测 3.98s 恰为 6.6 倍——**该断言自己违反了自己的注释**，既不可能因它声称的原因失败，又会在只是慢的机器上误报。

修法：把上界按实际工作量派生，`assert elapsed < 0.2 * 3 * 30`（18s）。30 倍余量仍远小于作业级 `timeout-minutes: 45`，真卡死必响。本机 `tests/unit/runtime/test_scheduler_parallel.py` ⇒ `2 passed in 0.34s`。

**教训（与 §2026-11-11 faulthandler 段同一类）**：CI 上任何「耗时上界」都必须从工作量派生并留足慢 runner 倍率，否则它检验的是机器快慢而不是它声称的性质；声称「不误报」的注释不构成判据。

## 2026-11-11 conftest 的 GUI 处置在 coverage 下导致段错误（已修）

**结论**：`tests/gui/test_main_window_contract.py` 在 coverage 下跑完 11 passed 后进程仍崩（Windows `0xC0000005` / Linux exit 139）的原因，是 `tests/conftest.py` 的 per-test fixture 在 teardown 里**强制派发 DeferredDelete**；不是产品逻辑缺陷，也不是 coverage 的 tracker 问题。**注意：这条销毁本身是对的、不能省**，见下方「修法（第一版，已回退）」。

**现象与定位链**

| 实验 | 结果 |
| --- | --- |
| 无 coverage 跑该文件 | `EXIT=0`（11 passed） |
| 有 coverage 跑该文件 | 11 passed 后 `EXIT=-1073741819`（栈 `Garbage-collecting` / `<no Python frame>`） |
| 单条用例（coverage） | `EXIT=0`；**两条即崩** |
| `COVERAGE_CORE` 默认 / `sysmon` / `pytrace` | 三种 tracker 全崩 ⇒ 与 tracker 实现无关 |
| `gc.disable()` / `gc.freeze()` / `DEBUG_SAVEALL` | 全崩 ⇒ 与 Python 循环引用回收无关 |
| 崩溃时机 | `pytest_unconfigure` 与 `atexit` 均已打印之后 ⇒ 解释器最终化阶段 |
| 最终化期存活对象（coverage） | 426 个 QWidget / 511 个 QObject（MainWindow / TaskCanvas / LoginSessionView / ChartView 等） |
| 把 conftest 的 per-test `_owned_qt_test_widgets` 覆盖成空 fixture | `EXIT=0`（11 passed） |
| 只覆盖 session fixture `_owned_qt_test_session` | 仍崩 |
| 只保留 teardown 里的 `shutdown()` + `_request_background_shutdown()` | `EXIT=1`（1 error，不崩） |
| 只保留 `deleteLater()` | `EXIT=1`（1 error，不崩） |
| 只保留 `QCoreApplication.sendPostedEvents(None, DeferredDelete)` | **崩** |

**修法（第一版，已回退）**：`_dispose_qt_test_widgets(app, widgets=None, *, flush: bool = True)`；per-test fixture 调用改传 `flush=False`（只 `deleteLater()` 标记），待删对象统一由 session 结束时的调用派发一次。**该修法违反既有契约，已回退**：`tests/unit/gui/test_qt_session_cleanup.py::test_per_test_cleanup_preserves_existing_widget_and_deletes_new_owner` 用「Per-test widgets must be deleted before the next theme change」要求 per-test 窗口在**下一个用例改全局主题前**就已真正销毁（`shiboken6.isValid(owner)` 为假）。远端 macos 腿当场判红（`AssertionError: Per-test widgets must be deleted before the next theme change`，`1 failed, 6057 passed, 53 skipped in 480.07s`）——**契约是对的**：「只标记删除」会让仍 valid 的窗口活到下一用例，语义上就是泄漏。

**修法（第二版，现行）**：不碰销毁时机，改「退出方式」。新增 `tools/ci_coverage_pytest.py`：`Coverage(config_file=True).start()` → `pytest.main(argv)` → `stop()/save()` → `sys.stdout.flush(); sys.stderr.flush(); os._exit(code)`。崩溃本来发生在**数据已落盘之后**的解释器最终化阶段，跳过最终化即可既保住 `.coverage` 数据又不崩。`quality.yml` 的 `gui-and-browser` 执行步改用它；三条全量 `test` 腿不出现该问题（全量跑必不崩），仍保留 `python -m coverage run -m pytest` 原生命令。

**第二版同时否决的替代方案**（都实测过退出码，避免以后重复试）：只派发待删控件自身的 `DeferredDelete`（仍崩）、逐用例 `gc.collect()`（仍崩）、`shiboken6.delete()` 立即销毁（仍崩）、`sendPostedEvents` 前后 `Coverage.current().stop()/start()`（仍崩，另引入 2870 warnings）、把 QApplication 挂到 `sys` 上活过最终化（仍崩）。

**验证**：coverage 下 `tests/gui/test_main_window_contract.py` `11 passed` `EXIT=0`；CI 精确三文件组合 `24 passed, 1 failed` `EXIT=1` 但**不再崩溃**（唯一失败是本机沙箱禁命名管道，CI 上无此限制）；`tests/gui` 整目录 311 passed；`tests/unit/gui/test_qt_session_cleanup.py` 2 passed（契约恢复）；`ruff` / `mypy` 对新增脚本均通过；全量 `tests` 在 coverage 下可跑完（`23 failed, 6046 passed`，23 个失败全为本机环境限制：缺 `OmniCrawler-market` 兄弟仓、Windows 无 Landlock、Git bash signal pipe、命名管道被沙箱禁用）。

**同时修掉的三平台唯一失败项**：`tools/check_anchor_integrity.py --update` 无条件 `DIGEST_PATH.relative_to(REPO_ROOT)`，测试把摘要路径 monkeypatch 到 `tmp_path` 时抛 `ValueError: ... is not in the subpath of ...`，令 `tests/unit/tools/test_anchor_integrity_gate.py::test_main_update_writes_digest` 在 ubuntu/windows/macos 上统一判红（`d73b6d55` 之后三条 `test` 腿的唯一失败）。改为仓内显示相对路径、仓外显示绝对路径；该测试文件 `19 passed`，真实 `--update` 输出不变。

## 2026-11-11 定位并修掉 `main` 上 test 作业连续超时取消的根因（模态 QMessageBox 无人应答）

**结论先行**：`test` 作业自 2026-11-09 起连续 5 次（run 1424 / 1428 / 1431 / 1434 / 1445）
在 `timeout-minutes: 45` 被取消、`gui-and-browser` 在 30 分钟处被取消，**根因不是死锁也不是性能回归**，
而是一条 GUI 用例在遍历导航时触发了**阻塞式模态对话框**，offscreen 环境下永远没人点「确定」。

**根因链（一手 faulthandler 栈）**。给 CI pytest 装上 `-o faulthandler_timeout=300` 的那个提交
（`abd75ee6`）上线后，`gui-and-browser` 作业在 13:04:51.278Z——启动后**正好 5 分整**——打印出
`Timeout (0:05:00)!` 与完整调用栈，栈顶依次为：

```
File ".../src/omnicrawler/gui/views/login_session.py", line 579 in _show_first_run_notice
File ".../src/omnicrawler/gui/views/login_session.py", line 271 in activate
File ".../src/omnicrawler/gui/main.py", line 919 in _on_nav_changed
File ".../tests/gui/test_main_window_contract.py", line 205 in test_navigation_switches_every_page
File ".../_pytest/python.py", line 187 in pytest_pyfunc_call
```

即 `test_navigation_switches_every_page` 会 `for row in main_window._nav_pages: main_window._nav.setCurrentRow(row)`
遍历**每一个**导航行，走到「登录会话」页时触发
`activate()` → `_needs_notice()` 返回真 → `_show_first_run_notice()` →
`QMessageBox.information(self, "登录会话说明（只提示一次）", notice_text(...))`。
模态事件循环在 `QT_QPA_PLATFORM=offscreen` 下无人应答，永久阻塞。

**为什么本地一直全绿、CI 必挂**：`_needs_notice()` 的判据是
`not bool(settings.value("session.notice_ack_v1", False, bool))`。
CI runner 是全新环境，该键从不曾存在 ⇒ 首次进入必弹 ⇒ 必挂。
本机该键**已被历史手工运行置为 `True`**，且本沙箱注册表不可写（`QSettings.isWritable()` 为假，
`remove()` 后读回仍是 `True`），因此本地连「全新环境」都造不出来。
这同时解释了历史上这条用例「时好时坏」——它取决于跑机器上有没有点过那个提示。

**修法**（只动测试，不动产品语义）：
1. `tests/gui/test_main_window_contract.py` 的 `main_window` fixture 增加
   `monkeypatch.setattr(LoginSessionView, "_show_first_run_notice", lambda self: None)`，
   并写清理由。本文件只验证「导航行 → 页面栈索引」映射；首次提示的语义判据本来就有
   `tests/unit/gui/test_login_session_view.py::test_notice_marker_persisted_in_settings`（用 `_FakeSettings` 替身）覆盖。
2. 新增**反向断言** `test_first_run_notice_guard_is_real_not_a_silent_noop`，同时断言两件事：
   产品侧该提示仍是模态 `QMessageBox.information`；fixture 执行后类上的方法**已被替换**
   （`__name__` 不再是原名）。方法改名或有人删掉那行 patch，此用例当场判红。

**踩过的坑（写下来避免复发）**：第一版守卫断言的是「调用它时没弹出对话框」。
但本机 `session.notice_ack_v1` 已是 `True`，`_needs_notice()` 返回假，`_show_first_run_notice`
根本不会被调到——于是**删掉 patch 后守卫依然通过**，等于没守住。判据必须避开会被环境状态掩盖的观测点，
改为直接断言「替换本身生效」。

**验证（本机一手）**：去掉 patch ⇒ 该用例 `1 failed`；加回 ⇒ 文件 `11 passed`；
`tests/gui` 整目录 `300 passed in 41.44s`；`tools/self_verify.py --set static`
结论不变（28 通过 / 1 失败，唯一失败仍是 `check_shell_scripts` 的 Git bash signal-pipe 沙箱限制）。
**待远端确认**：`abd75ee6` 之后的 run 里三条 `test (…)` 腿是否恢复全绿——本地无法复现原挂起，
这条结论要由 CI 终态背书。

## 2026-11-11 CI pytest 统一装 faulthandler 超时（把「黑箱超时」变成可定位）

**问题**：`main` 上 `test` 作业（`quality.yml`）自 2026-11-09 起连续 5 次在
`timeout-minutes: 45` 处被取消，`gui-and-browser` 在 30 分钟处被取消。取消日志的
形态是确定性的：pytest 启动约 40 秒后输出恰好 72 个点 + `[1%]`，随后到取消为止
**43 分钟零输出**（ubuntu job 130448299484 与 windows job 130448299605 形态一致）。
作业最终状态只有 `cancelled`，**没有栈**——既不指向哪条用例，也无法区分「用例挂起」
与「fixture/库内部死锁」。对照最后一次全绿 run 1418（`b9a6e686`，2026-11-05）
`5777 passed, 48 skipped in 394.88s`，说明这是 11-05 之后的回归。

**做法**：给 CI 里**每一个**真正执行用例的 pytest 入口加 `-o faulthandler_timeout=300`。
单条用例超过 5 分钟即把调用栈打进日志，把「零输出挂起」变成「可定位的挂起点」。
只加可观测性：判定逻辑一字未改，超时仍由作业级 `timeout-minutes` 兜底；
300s 远高于实测最慢单条用例（本机 `20.22s`，Windows 实测 27–29 分是**整个 job** 而非单条）。

**覆盖点（7 处）**：

| 位置 | 执行步 |
| --- | --- |
| `quality.yml` | `core_tests`（三平台全量 pytest）、`gui-and-browser` 子集 |
| `e2e.yml` | Qt-free 冒烟（`tests/unit/gui/test_log_parser.py` 等） |
| `market-compatibility.yml` | `tests/unit/plugin` |
| `plugin-sandbox.yml` | Unix 沙箱套件、契约与穿透套件（两处） |
| `e2e/run.py` | `--full-regression` 全量回归、带 coverage 的 e2e 套件 |

**守卫**：`tests/unit/utils/test_ci_trigger_contract.py`
- `test_every_ci_pytest_run_arms_faulthandler_timeout`：遍历 `.github/workflows/*.yml`
  全部执行步（排除 `--collect-only`），缺标志即判红并把违规命令打出来。
- `test_e2e_runner_also_arms_faulthandler_timeout`：覆盖不在 YAML 里的 `e2e/run.py` 两个执行点。
- `test_faulthandler_guard_detects_a_step_that_drops_the_flag`：**反向断言**——把带标志的
  命令去掉标志后，判据必须识别为缺失；同时断言收集步不被误当执行步。

**加守卫当场发现的既有缺口**：首次运行该守卫即判红，报出 4 个此前遗漏的执行步
（`e2e.yml` 1 个、`market-compatibility.yml` 1 个、`plugin-sandbox.yml` 2 个），
说明「CI 只看得到最终 `cancelled`、看不到栈」是系统性的而非单点失误。

**验证（本机一手）**：`tests/unit/utils/test_ci_trigger_contract.py` = `5 passed`；
反向断言在去掉标志时确实判红（首次运行即 `1 failed` 并列出 4 条违规）。
`quality.yml` 等 4 个 YAML 均 `yaml.safe_load` 通过（9 个 jobs），行尾 CRLF、`bareLF=0`。

**这次投资当场回本**：该提交 push 后，同一分支的 `gui-and-browser` 作业在启动后 5 分整
打印出 `Timeout (0:05:00)!` 与完整调用栈，直接定位到真正的根因（见下一节
「定位并修掉 `main` 上 test 作业连续超时取消的根因」）。在此之前，同一现象只能看到
`[1%]` 加 43 分钟零输出。

## 2026-11-11 `test_main_window_contract.py` 反向断言用例缺 QApplication（子集运行必崩）

**症状**：`tests/gui/test_main_window_contract.py` 单独跑或与少量文件一起跑时，用例全部
`passed` 后进程以 `0xC0000409`（`-1073740791`，Windows）或 `0xC0000005`
（`-1073741819`，Garbage-collecting 期 access violation）中止；`pytest` 报告里
可能一条失败都没有。全量跑（`pytest tests`）反而正常——因为前面的 GUI 用例
已顺手创建了 `QApplication`。

**根因**：两个**反向断言**用例（`test_wiring_detector_rejects_a_deliberately_dead_action`、
`test_navigation_map_check_rejects_a_dangling_row`）不取 `main_window` fixture，
却直接构造 `QMainWindow()` / `QStackedWidget()`。没有 `QApplication` 时 Qt 直接
`qFatal("QWidget: Must construct a QApplication before a QWidget")`；只把 app 存成
局部变量时又会被 GC 连带销毁控件树，pytest 退出期踩 native access violation。

**修复**（`tests/gui/test_main_window_contract.py`）：新增模块级 `_APP` 与
`_ensure_app()`（`QApplication.instance() or QApplication([])`，模块级强引用），
`main_window` fixture 改用它，两个反向断言用例在构造 widget 前显式调用。
判据是「单跑也绿」，而不是「全量跑恰好绿」——这正是不加 fixture 就漏掉的形态。

**验证（本机一手）**：

- 两个反向断言用例单跑：各 `1 passed`，退出码 0（修复前分别 `-1073740791`）。
- `tests/gui` 整目录 299 passed；带 `coverage` 同样 299 passed（32.89s）。
- 修复不动任何产品代码，`python tools/self_verify.py --set static` 结论不变。

**仍未闭环的相邻现象（已登记，勿误判为本修复未生效）**：本机以
`coverage run -m pytest tests/gui/test_main_window_contract.py`（**带 coverage、只跑这一个
文件**）时，10 个用例全绿后仍在 Garbage-collecting 期 `0xC0000005` 中止；去掉
`coverage` 则退出码 0。逐用例 `-k` 单跑 10 条各自退出码全为 0，故不是某一条用例的
断言问题；本机不再深挖（属 coverage 跟踪 + Qt 包装对象析构顺序的组合，CI 用的是
同一 coverage 调用形态，但 CI 上是 45 分钟**无输出挂起**而非数秒崩溃，两者未证同源）。
排查方法见下：`.test-tmp/shimpkg/sitecustomize.py` 把 `os.mkdir` 的 `0o700` 抬成
`0o755` 以绕开沙箱 ACL，且 `TMP/TEMP/TMPDIR` 必须落在**仓库根之外**
（`E:\tool\biancheng\omnicrawler\.test-tmp\run_*`）——放进 `OmniCrawler\` 内会让
`core/config.py` 的 `pyproject.toml` 根探测把 workspace 塌缩到真实 `work/`，
导致打包类用例读写 6000+ 个历史任务目录并误报哈希不一致。

## 2026-11-11 dependabot 收口：工具链六项续升 + 两个 Actions SHA 升级

8 个长期悬空的 dependabot PR（#175 / #176 / #177 / #179 / #180 / #181 / #182 / #183）本批一次收口。
其中 6 个改同一个 `constraints/quality.txt`、指向同一组行，**逐个 squash 合并必然互相 behind**，
且 #183 属于「审查不通过」形态：它把 `uv` 升到 `0.13.7` 却没同步 4 个 reusable 工作流的
`setup-uv version:` pin —— 正是 `test_release_build_toolchain_uv_version_matches_constraints`
守的那个复发缺口（#158 / #151 / #152 同型，2026-09-25 那次就是这么只改了约束）。因此本批不是
机械合并，而是先按仓库纪律把缺口补齐再合并。

实际落地（`constraints/quality.txt`）：

- `pip 26.2.2 → 26.3`、`uv 0.13.6 → 0.13.7`、`pytest 9.1.1 → 9.2.0`、`coverage 7.17.0 → 7.17.2`、
  `ruff 0.17.3 → 0.17.4`、`mypy 2.4.0 → 2.4.1`。
- 4 个 reusable 工作流的 `setup-uv` `with.version` 全部 → `"0.13.7"`
  （`reusable-build-linux.yml:60` / `reusable-build-macos.yml:64` / `reusable-build-windows.yml:57` /
  `reusable-finalize-release.yml:43`），满足单一真源守卫。
- `.pre-commit-config.yaml` 的 ruff `rev: v0.17.3 → v0.17.4`、mirrors-mypy `rev: v2.4.0 → v2.4.1`。
  这两个 rev 目前**没有任何测试或门禁校验**（`git grep pre-commit-config -- tests tools .github` 零命中），
  靠的是文件内注释「rev follows constraints/quality.txt」的自律口径，故属一致性修正而非门禁驱动。
- GitHub Actions 两处 pin 升级：`astral-sh/setup-uv` → `v10.3.3`
  （commit `68559e99cc807c1eb7e9238fd43a15bba460eec2`，4 个 reusable 工作流）；
  `softprops/action-gh-release` → `v3.0.4`（annotated tag，解引用后 commit
  `8a0cd651e5b2365bd640854a3fa5dfc70e4875f3`）。两者都保持 commit SHA pin 纪律，不用浮动 tag。

版本真实性核对（一手）：

- PyPI JSON 200 且 `yanked: false`、非预发布：`uv 0.13.7`（2026-11-03）、`ruff 0.17.4`（2026-11-05）、
  `mypy 2.4.1`（2026-10-28）。`coverage 7.17.2` / `pytest 9.2.0` / `pip 26.3` 由对应 dependabot PR
  的 GH checks 全绿间接佐证。
- pre-commit tag 存在性：`astral-sh/ruff-pre-commit` `v0.17.4` → `c0f278f1254ccc8fb09676bcdb8d847e7db493e5`；
  `pre-commit/mirrors-mypy` `v2.4.1` → `ad26ebfa797fcb9b8f83275937c1c1d6e126719c`。
- `astral-sh/setup-uv` `v10.3.3` → `68559e99cc807c1eb7e9238fd43a15bba460eec2`（与 #175 的 pin 逐字相同）；
  `softprops/action-gh-release` `v3.0.4` 的 annotated tag 对象 `28ab305d0ffa26d60ec4bd614126329e26c7a0ec`
  → `.object.sha = 8a0cd651e5b2365bd640854a3fa5dfc70e4875f3`（与 #179 的 pin 逐字相同）。

`uv.lock` 无需重解：它与 `constraints/quality.txt` 是**不同真源**（前者是便携构建依赖，
后者是 CI 裸装工具链约束），锁里 `coverage` 仍是 `7.16.0`、`mypy` 仍是 `2.3.1` 属正常既存差异。

自证手段与边界（诚实登记）：本机 pytest 被沙箱 ACL 挡住（`PermissionError: [WinError 5]`
在 `_pytest/pathlib.py:354 cleanup_dead_symlinks`），三平台测试腿与
`test_release_workflow_contract.py` **无法在本机复跑**，因此本批的守卫满足性靠
「直接读 `constraints/quality.txt` 派生 `uv==0.13.7` + 逐字比对 4 个工作流 pin」的等价静态核对，
以及 `python tools/self_verify.py --set static`；真正的三平台验证由本次推送到 `main` 后的远端 CI 承担。

## 2026-10-30 门 3 口径收口：本地 audit 升 error + GUI 投稿前自查

分叉形态（此前只记录、未改）：主仓 `plugin_audit.gate_dependencies_consistency` 对「缺
`PLUGIN_METADATA.dependencies`」报 `warning` 后按 `declared=[]` 继续跑；市场投稿校验器
（`validate_submission.py:209`）对同一情形是 `ValueError` 硬拒；而 GUI 投稿路径
`market_home._on_upload` 直接 `build_plugin_submission`，**从不跑 audit** ⇒ 作者本地一路"过"、
提交后由市场 CI 拒收。市场仓 `8110658` 把门 3 下沉进投稿校验器、且 `market-ref.txt` 已推进到
`f33fcac`（#170），这条分叉从"隐患"变成"必然踩到"。

改法（三处，互相咬合）：

1. `gate3_dependencies_missing` 由 `warning` 升 `error`，消息补「市场投稿会直接拒收」——
   与该函数 docstring 自己写的「必填」一致；
2. 新增 `plugin_market_logic.pre_submit_blockers(plugin_dir)`（无 Qt 依赖的纯函数，只看 error 级），
   `market_home._on_upload` 在打包前调用，命中即弹窗逐条列出并中止上传；
3. 夹具同步：`tests/unit/plugin/test_plugin_audit.py::_make_plugin` 补 `'dependencies': []`
   —— 它造的那个 "good" 插件按新口径本来就该合法（此改动也是全量回归里唯一一处连带）。

**变异实验**：把 level 改回 `warning` ⇒ `test_gate3_missing_dependencies_is_error_like_market`
与 `test_missing_dependencies_blocks_submission` **双双红**（11 通过 / 2 失败），恢复后 13 通过
⇒ 证明 GUI 守卫真的依赖 audit 口径，不是两条互不相干的死断言。

影响面（有意为之）：`plugins audit --local <dir>` 对这类插件现在退出码 **1**（此前 0）。
这是 fail-fast：市场侧本来就会拒，早一点告诉作者更好。

## 2026-10-30 市场快照推进到 f33fcac（并修掉「审计读错载荷」）

用户点头「市场快照继续推进」。钉值 `8744302…` → **`f33fcac397e050d02e7fb2ae80cd93504fd19697`**，
同步器自报 `--check` 通过（`OK registry: 9 个插件 + 15 个模板清单一致，签名校验完成`），
写盘结果 `更新 2 项，删除 1 项`：

| 文件 | 变化 |
|---|---|
| `constraints/market-ref.txt` | 8744302… → f33fcac… |
| `market/catalog.json` | 8 个插件版本各 +2 minor（academic-paper 0.6.4→0.8.0、chronicle-capsule 0.2.0→0.4.0、community-guide 1.0.0→1.2.0、issue-wishlist 1.0.0→1.2.0、lumen-drift 0.4.0→0.6.0、signal-sieve 0.2.0→0.4.0、tideprint-gate 0.2.0→0.4.0、upstream-lantern 0.1.0→0.3.0）；模板 15 条未变；`schema_version` 仍为 1；`sequence` 升 |
| `market/authors/starlife-creator.yaml` | 注释里的创作者公钥改为 `4c3014804d85…` |
| `market/keys/5dcd59f7….pub.pem` | **删除** |

删钥单独核过：该键由市场仓 `f8b6f46`（2026-10-25）主动删除，同步后 `catalog.json`/`authors/`/`keys/`
里对 `5dcd59f7` 的引用 **0 命中**，新键 `4c3014804d85…pub.pem` 在位 ⇒ 属镜像语义，不是丢文件。

守卫从红转绿：`test_market_catalog_cross_repo.py` + `test_sync_snapshot_pin.py` = **7 通过**
（推进前 `test_snapshot_plugins_match_market_repo` 是红的唯一一条）。

### 顺带修掉：`tools/audit_paper_scenario.py` 一直审的是根目录遗留载荷

推进后重跑审计，发现 SHA 与文档里 0.6.4 时代那条**完全一样**（`5e8628b3…`），而插件已到 0.8.0 ——
工具硬编码 `directory / "plugin.py"`。实测市场仓同时存在：

- 根 `plugin.py`：`5e8628b3…`，根 `package.manifest.json` 版本 **0.3.0**，两者**不一致**；
- `versions/0.8.0/plugin.py`：`a6b87214…`，`versions/0.8.0/package.manifest.json` 版本 0.8.0，**一致**；
- `market.yaml` 的 `plugin_file` 指向后者 —— 用户安装时下载的也是后者。

⇒ 此前的「清单与源不一致」结论是**读错文件**的产物。修法：按 `market.yaml` 的
`plugin_file` / `package_manifest_file` 解析（无 `market.yaml` 时回退扁平布局），报告新增
`source_file` 字段；边界用例加布局守卫 —— `source_file` 必须落在 `versions/<manifest_version>/`，
退回根目录遗留副本即红（`tests/unit/plugin/test_paper_scenario_boundary.py`，含两条
`resolve_payload` 单测）。修后：

```
source_sha256   a6b87214e25e7066dbecd8b34228a3f2d1affdc087c46d22fa0985083ee1927c
source_file     …/versions/0.8.0/plugin.py
manifest_version 0.8.0   manifest_source_matches true
direct_network_modules ["httpx"]   decision stop_plugin_route
```

**结论不变**（0.8.0 仍直连 httpx、仍自建 `sync_playwright()` 两处、Cookie 仍走
`_state_get` 的插件 state 命名空间），但依据换成了真载荷；`docs/PAPER_DOWNLOAD_ACCEPTANCE.md`
已按新证据重写并留更正记录。

## 2026-10-30 GUI 隐私用例污染开发者真实配置（那条「环境型失败」的真因）

§前面把它记作「`AppSettings` 读真实用户配置目录，属用例没隔离用户状态」—— 复核后**定性要升级**：
它不只是没隔离，而是会**改坏开发者机器上的真实隐私配置**。

机制（逐环可定位）：源码模式下 `make_qsettings` 走 `QSettings("OmniCrawler", "OmniCrawlerGUI")`
⇒ Windows 落 HKCU 注册表（`src/omnicrawler/gui/settings.py:36`）；同文件
`test_privacy_redact_setter_persists_across_instance` 置 `privacy_redact_enabled = False` 并
`sync()`（`tests/unit/gui/test_privacy_settings.py:48-49`），而原 autouse 夹具只做
`AppSettings.reset()`，**不还原落盘值** ⇒ 跑一次测试，用户自己的日志脱敏就关掉，
且下一趟真实运行不再脱敏；顺带把只读默认值的
`test_privacy_defaults_match_product_policy` 污染成 `assert False is True`。

本机取证：`reg query "HKCU\Software\OmniCrawler\OmniCrawlerGUI\privacy"` →
`redact_enabled REG_SZ false`（产品默认 true）。这与「该机跑过本文件用例」一致，但**不单独归因**——
在 GUI 里手动关掉也是同一个值，注册表不带来源。要修的点是机制，不是这个值本身。

修法：autouse 夹具把落点重定向到 `tmp_path`，**走 `is_frozen()` 那条已测的便携分支**
（不能用 `QSettings.setPath`/`setDefaultFormat` —— `make_qsettings` docstring 与本仓
`test_portable_write_boundary.py:4-6` 都实测记录过它们对 `QSettings(org, app)` 构造在
Windows 上不生效）。

证据：
- 隔离后 `tests/unit/gui/test_privacy_settings.py` + `test_portable_write_boundary.py` = 8 通过；
- 写入落点实测：`--basetemp` 下生成 `<bt>/test_privacy_redact_setter_per0/settings.ini`
  内含 `redact_enabled=false`，注册表未被本次运行改动；
- **变异实验**：摘掉夹具两行 `monkeypatch.setattr` ⇒ 默认值用例立刻红回
  `assert False is True`，证明是夹具承重而不是运气。

同一次核查查出**第二处同类污染**：`tests/gui/test_audit_20260805_fixes.py::
test_template_favorites_go_through_app_settings` 往真实设置写 `favorite_templates=["demo"]`
且不还原（该机注册表现值 `templates/favorites REG_MULTI_SZ demo`；跑前没留快照，
不单独归因，但这个赋值只出现在这条用例里）。那里**不做 autouse** —— 本文件多数用例要
构造真实 `MainWindow`，而 `is_frozen()` 同时决定数据根口径，全局改写会牵动无关行为；
改成给这条用例单独挂 `tmp_settings_store`。该文件 17 通过与改前持平，favorites 写入实测
落在 `<basetemp>/test_template_favorites_go_thr0/settings.ini`。
同文件 `:238`/`:248` 两处已把 `_settings` 整体换成抛 `RuntimeError` 的 `_DestroyedQSettings`
（`value`/`setValue`/`sync`/`isWritable`/`status` 齐），不落真实存储。

## 2026-10-30 跨仓投稿 fixture 补齐（门 3 必填 + finalize 参数面）

市场仓把门 3（`dependencies` 必填、零依赖显式 `[]`）下沉到投稿校验器（市场 `8110658`），
又给 `finalize_submission` 加了 `--reviewer` / `--review-comment` / `--review-decision`
（市场 `6ee7044`）。主仓两处用例仍按旧契约造夹具，**本机跑活的市场仓检出即红**：

- `test_submission_validator.py`：`PLUGIN_METADATA` 缺 `dependencies` →
  `ValueError: … PLUGIN_METADATA.dependencies 未声明（零依赖请显式填 []）（门 3）`；
- `test_finalize_submission.py`：`SimpleNamespace` 缺属性 →
  `AttributeError: 'types.SimpleNamespace' object has no attribute 'reviewer'`，补一个又冒一个
  （`review_decision`），共 3 个。

补法：fixture 声明 `'dependencies':[]`；新增 `_finalize_args()` 对齐市场 CLI 默认值，
市场侧再加旗标时只改一处。

钉版两侧惰性（这是 CI 仍绿、且本改动不会把 CI 打红的原因）：
- `constraints/market-ref.txt = 8744302` 那版 `validate_submission.py` **没有** `dependencies`
  这条门，也不拒绝未知键（`git show 8744302:tools/validate_submission.py | grep dependencies`
  命中 0 行）；
- 同一版 `finalize_submission.py` 只读 `args.{submission_dir, reviewed_manifest_sha256,
  market_id, maintainer_key}` 4 个属性 ⇒ 多出来的 3 个字段是惰性的。

新护栏（会真红）：`test_finalize_args_helper_covers_market_cli_surface` 用正则扫市场脚本里
所有 `args.*`，断言 fixture 的属性面是其**超集**。变异实验：删掉 `review_decision` 一行，
该用例报 `市场侧新增旗标未进 fixture: ['review_decision']`，恢复后 6 条全绿。

本机口径：`tests/unit/plugin` = 467 通过 / 1 失败 / 3 跳过，`self_verify --set static` = 23/0/0。
唯一失败 `test_snapshot_plugins_match_market_repo` **与本改动无关**，见下。

## 2026-10-30 快照落后：主仓内置市场快照比市场 HEAD 少两个 signed release

把同级 `OmniCrawler-market` 拉到 `f33fcac` 之后，`test_snapshot_plugins_match_market_repo`
转红——不是回归，是这条守卫第一次真说话（此前本机没有市场仓检出，整组用例 skip）。

- 落后量：`8744302..f33fcac` = **14 个提交**，其中 `8fb0b34`、`34def4a` 两个是
  `publish: signed … updates for eight plugins`，动了 `catalog.json` 与 `plugins/`。
- 条目级差异（节选）：`chronicle-capsule` 0.2.0→0.4.0、`issue-wishlist` 1.0.0→1.2.0
  （权限面从 `['network:scoped']` 扩到 `+state:read +state:write`）、
  `academic-paper-downloader` 0.6.4→0.8.0；`updated_at` 从 8/9 月推到 2026-10-04；
  所有签名/清单文件路径改走 `versions/<v>/` 布局，`package_manifest_sha256` 全部换值。
- ⇒ **主仓随安装包分发的离线市场快照，内容是 10-01 之前的一版**，九个条目 id 仍一致
  （所以 id 列表断言先过，条目内容断言才红）。
- CI 不受影响：CI 按 `market-ref.txt` 检出 `8744302`，快照与之一致 ⇒ 恒绿。
- 已做（2026-10-30 用户点头，见上一节）：推进 `market-ref.txt` 到 `f33fcac` 并跑 `tools/sync_snapshot.py`。
  **更正**：这里原先写「把八个插件的新版本连同签名装进主仓产物」，**不对** —— `market/` 只镜像
  `catalog.json` + `authors/` + `keys/`（纯元数据；实测 `find market -name plugin.py` 为空），
  插件载荷仍是安装时从市场仓取。同步实际只改目录元数据与一把被市场仓淘汰的作者公钥。
  钉值消费者仍是 `AGENTS.md`、`constraints/README.md`、`test_plugin_audit.py:158`、
  `tools/test_sync_snapshot_pin.py`、`docs/PAPER_DOWNLOAD_ACCEPTANCE.md`（后者已随之重基线）。
- 严重性次一级的口径分叉顺带记录：同一门 3，主仓 `plugin_audit.gate_dependencies_consistency`
  是 `level="warning"` 后 `declared = []` 继续跑，市场投稿校验器是 `ValueError` 硬拒 ⇒
  作者本地 audit 过了、投稿 CI 才被拒；且 GUI 投稿路径（`market_home._on_upload`）当时压根不调 audit
  —— 这两点已在「门 3 口径收口」一节处理（audit 升 error + 投稿前自查）。

## 2026-10-30 三处 Action 引用的形态核对（#159 / #160 / #163）


三个 PR 都不是"版本有没有问题"，而是**引用写法与本仓约定不一致**，逐个用上游 ref 与 CI 日志核对：

| PR | 原引用 | 核对结果 |
|---|---|---|
| #163 | `trivy-action@a9c7b0f0… # v0.36.0` | `a9c7b0f0` 是 **v0.36.0 注解 tag 对象的 SHA**（`commits/a9c7b0f0` 实测 422 "No commit found"，`git/tags/a9c7b0f0` 返回 tag 对象，其 `object.sha = ed142fd0`）。原写法**能跑**（run 40983833402 的 `docker` 作业下载日志实证：`Download action repository '…@a9c7b0f0'`），但它不是 commit ⇒ 换成 `ed142fd0…`，同一发行版，形态对齐 |
| #160 | `dependency-review-action@v4` | 浮动 major 引用。job 123830119816 日志实证 Actions 把它解析到 `2031cfc0…`（= v4.9.0），作业**真的跑了**，只因 Dependency graph 未启用而报 `##[error]Dependency review is not supported on this repository`。改成 v5.0.0 的 commit `a1d282b3…` + 版本注释 |
| #159 | `gitleaks-action@v2` | v2 解析到 `ff98106e…`（job 123837305938 日志），v3 tag 存在（`e0c47f4f…`，同时是 v3.0.0）。license 问题实测过：同一作业打印 `[Starlife-creator] is an individual user. No license key is required.` ⇒ 不需要新增 secret |

⚠ 一处我自己差点写错的推断：看到 `GET /repos/…/git/ref/tags/v4` 返回 404 就差点断言"`@v4` 解析不了、这一步从没跑过"。
CI 日志证明它解析到了 v4.9.0 —— **API 的 ref 查询与 Actions 的解析不是一回事**，涉及"这步到底有没有跑"必须看作业日志。

依赖关系提醒：`dependency review` 仍是提示性检查（`continue-on-error`），它的实际覆盖面要等 Settings → Code security
里打开 Dependency graph 才有；那是一次仓库设置改动，本批没动，留给需要它真阻断的时候。

## 2026-10-30 工具链续升（uv 0.13.6 / ruff 0.17.3）与守卫复现

#158 只改 `constraints/quality.txt`，与 #151/#152 完全同型：`_pinned_uv_version()` 从约束文件派生期望值，
断言 4 个 reusable 工作流的 `setup-uv version:` pin 逐字相等 ⇒ 单改约束必然让三条平台测试腿红。
本批把 pin 与 `.pre-commit-config.yaml` 的 ruff `rev` 一起同步。

本机实测：
- `uv 0.13.6` / `ruff 0.17.3` 均在 PyPI（JSON 200），发布日期 2026-10-27，且**都不是 RC**
  （与 twisted 那条"唯一修复版是预发布"的处置口径一致：RC 不进交付依赖）。
- `ruff 0.17.3 check src tests tools` = All checks passed（与 0.17.2 同结果，豁免预算不变）。
- `uvx uv@0.13.6 lock --check` 退出码 0 —— `uv.lock`（现在 revision 5）无需重解。
- `ruff-pre-commit` 的 `v0.17.3` tag 存在（`f368fb36…` 是 v0.17.2，本次核对 v0.17.3 = `25bcf950…`）。

一处**与本次改动无关**的现象值得记录，避免下一个人重踩：新建 worktree 后 `git status` 把
`src/omnicrawler/gui/accessibility.py` 报成已修改，但 `git diff` 里两侧文本一模一样。
按字节核对是**纯行尾差异**（该文件在 HEAD 里本就是混合行尾：committed blob 有 5 行 CRLF，
checkout 出来 42 行 CRLF，而 `core.autocrlf=true`）。它不是任何人的编辑，
因此本批只 `git add` 明确点名的文件，绝不用 `git add -A`。

未在本地验证：三平台全量 pytest、`.[full]` 装机与审计 —— 由本批 CI 判定。

## 2026-10-30 selectolax 后端迁移到 lexbor（含一次"放宽上界"的真相纠正）

上一批把 `selectolax <1→<2` 判成"需要后端迁移，不属于依赖升版"而否决。本批做掉那次迁移，
并**只按 lexbor 走，不为旧后端写兼容**（用户定案）。

改动：`change_detector._extract_content` 用 `selectolax.lexbor.LexborHTMLParser`；
extras `selectolax>=0.2,<1` → `>=0.4.2,<2`（下界抬到 lexbor 存在的版本：0.4.2 起有，实测过）；
撤销 dependabot 的 semver-major 忽略；`uv lock --upgrade-package selectolax` 把锁定环境
从 0.4.11 升到 **1.0.2**（否则放宽只在 pip 安装腿被测到，`uv sync --locked` 那条路永远跑旧版）。

### 为什么必须有静态守卫，而不是只靠 mypy

本机变异实验：把源码改回 `from selectolax.parser import HTMLParser` 后，
`tests/unit/test_change_detector.py` 的 **57 条用例全部照旧通过**，只有一条新用例红
（`test_html_backend_is_lexbor`）。原因是这个调用点包在 `try/except ImportError` 回退链里：
旧后端在 1.x 不存在 ⇒ import 抛 ImportError ⇒ 静默降级到 lxml/BS4，**输出仍然正确**。
所以"用了哪个后端"这件事行为上不可见，只能当显式契约钉住。

### 抽取行为是否变化（关系到已存快照哈希）

三个组合逐字比对，10 个刻意挑易差异的样例（`&nbsp;`、`<br>` 相邻、未闭合 `<li>`、表格单元格、
`<script>` 过滤、`&lt;tag&gt;` 转义、未加引号属性、`hidden`、未闭合标签、`<textarea>`）：

```
modest 0.4.11（venv pyd）  ==  lexbor 0.4.11（venv pyd）  ==  lexbor 1.0.2（Temp/slx1 pyd）
```

三者输出**完全一致**，且每次运行都打印了实际加载的 `.pyd` 路径来证明不是"三份都在跑同一个后端"。
⇒ 已存快照哈希不会因为升级而集体误报"变化"。
限定语要说清：这是我构造的 10 个样例，不等于任意网页上两个引擎的 DOM 修复永远一致
（它们本就是两个不同的解析引擎）；真出现差异时表现是"某页被报一次变化"，不是错误。

### 本机新暴露的一件事：跨仓门 3 的版本差（与本次改动无关）

`tests/unit/plugin/` 有 3 条用例在本机红、在 CI 绿，**不是本批引入的**（在未改动的 main 检出上同样红）：
`test_finalize_submission.py::test_maintainer_finalize_preserves_creator_package_and_publishes`、
`test_submission_validator.py::test_market_submission_validator_accepts_gui_payload`
与 `..._accepts_declarative_view_permissions`。

真实原因（读报错读出来的，不是猜的）：这三条会 subprocess 调用**同级市场仓检出**的
`tools/validate_submission.py`，而市场仓 main 已经带上门 3「`PLUGIN_METADATA.dependencies`
必填，零依赖要显式填 `[]`」（由主仓 #42 那批下沉的规则）；本仓测试 payload 没声明 `dependencies` ⇒ 被拒。
CI 之所以绿，是因为主仓按 `constraints/market-ref.txt` 钉的是 **8744302**（早于门 3 下沉的市场快照）。

⇒ 两件事待办，性质不同：① 把这 3 条 fixture 补成合规投稿（声明 `dependencies: []`），
这条改法对旧/新两版校验器都成立；② 要不要把 `market-ref.txt` 从 8744302 推进到市场仓当前 main
（`f33fcac`）是一次跨仓快照推进，会同时把市场仓新门规带进 CI，需单独一轮验证 —— 不是顺手就能改的。

## 2026-10-30 pip-audit 基线清空 pillow 一节 + 计数口径修正 + dependabot 忽略 selectolax 大版本

三件事都来自 #153/#157 那批实测出来的副产品，不是新猜的。

### ① 39 条 pillow 告警整段删除（不是继续放行）

它们存在的唯一原因是我们自己钉的 `Pillow>=10,<13` 把 `.[full]`（= pip-audit 扫的那套环境）
压在无补丁版的 12.3.0 上。放宽到 `<14` 之后，PR #153 同一条 run 的
`windows-full-dependency-matrix`（job 123815597720，success）实测：

```
Collecting Pillow<14,>=10
... Pillow-13.0.0
pip-audit 已审基线：忽略 94 条告警（新增漏洞仍阻断）
No known vulnerabilities found, 8 ignored
```

`8 ignored` = 只剩 twisted 那 8 条，39 条 pillow **一条都不再命中**。外部数据源同一口径
（OSV `api.osv.dev/v1/query`，2026-10-30）：pillow 12.3.0 → 59 条已知漏洞，13.0.0 → 0 条。

⇒ 留在基线里只会做一件坏事：将来任何约束把 pillow 退回 12.x 时**不阻断**。删除后它会重新阻断。
反向确认：`pip-audit --local --ignore-vuln CVE-9999-99999 --ignore-vuln PYSEC-9999-99999`
本机退出码 0（`No known vulnerabilities found`）—— 删与不删都不会让门禁误红，所以这一步是纯口径修正。

### ② 计数报错了（顺手修）

上面那行"忽略 94 条告警"实际是 47 个 id：每个 id 展开成 `--ignore-vuln` + id 两个参数，
原脚本把**数组长度**当成了条数。改为单独计 `n_baseline`。本机用同一段 bash 模拟验证：
`n_baseline=8 args=16`（本批删除后的真实数字）。

另外这个作业跑在 **windows runner** 上，`grep` 出的 id 若带 `\r` 就匹配不上任何告警 ⇒
基线**静默失效**（既不放行也不说为什么）。补了 `tr -d '\r'`，并用 CRLF 样例做过对照。

### ③ dependabot 不再提 selectolax 大版本

1.0 删了 `selectolax.parser`（Modest）后端，`change_detector.py:324` 的快路径装上是静默降级
而不是报错（详见上一节 #157 的实测）。只忽略 `version-update:semver-major`，0.x 的补丁/次版本
照常提；写法沿用仓内 `PyYAML` 那条「理由写进 ignore 块」的既有格式。

**为什么现在不加 `Pillow<13` 之类的反向保护**：上界放宽本身已由 #157 落地并带锁刷新，
再叠一层 ignore 会把"退回 12.x 应当阻断"这条信号也一起屏蔽掉。

未在本地验证：改后的 audit 步骤需在真实 windows runner + `.[full]` 装机上跑（本批 CI 判）；
dependabot 配置只在下一轮扫描才生效，无法本地验证。
## 2026-10-30 extras 上界放宽（3 项采纳 / 1 项否决）+ 锁文件同步刷新

#153 把 4 个 extras 上界放宽（`selectolax <1→<2`、`Pillow <13→<14`、`duckdb <2→<3`、
`pyarrow <26→<27`），dependabot 只能看见 `pyproject.toml`，没重解 `uv.lock`，
`dependency lock / reproducible install` 腿因此报红：

```
error: The lockfile at `uv.lock` needs to be updated, but `--check` was provided.
```

（run 40979465961 / job 123815598214）

同一 run 的三条平台 test 腿各装出 `duckdb-2.0.0`、`pillow-13.0.0`、`pyarrow-26.0.0`、
`selectolax-1.0.2`，**5767 passed** 且覆盖率门禁通过，mypy 只报**一条**错误：

```
src/omnicrawler/scheduling/change_detector.py:324: error: Module "selectolax.parser"
has no attribute "HTMLParser"  [attr-defined]
```

本机用 `pip install --target` 隔离装 selectolax 1.0.2 实测（不靠 mypy 的措辞推断）：
`import selectolax.parser` 直接抛 `ImportError`，文案是「Modest backend is deprecated since
selectolax 1.0 … 请改用 `from selectolax.lexbor import LexborHTMLParser`」。⇒ 放宽 selectolax
需要一次**后端迁移**（现在这条快路径会静默降级到 lxml/BS4），不属于依赖升版，**本批否决这一项**。

本批采纳另外 3 项 + `uv lock` 刷新。

本机证据：
- 锁刷新**不动任何包版本**：`git diff -U0 uv.lock` 里 `^[-+]version = ` 命中 0 行；变更只有
  specifier 记录（pillow/duckdb/pyarrow 各 3 行）、`revision 3→5` 与 resolution-markers 排序。
- `uvx uv@0.13.5 lock --check` 退出码 0；`tools/check_lockfile_consistency.py` 通过
  （它比对 pyproject 与锁的版本/requires-python/依赖/extras）。
- revision 5 向后兼容实测：本机 `uv 0.12.3 lock --check` 对该锁退出码 0（读旧写新，不炸）。
- `self_verify --set static` = 22 通过 / 0 失败 / 1 跳过（缺同级 `../OmniCrawler-market` 检出，CI 有）。
- ★ Pillow 的真相来自 main 自己的 CI 日志（run `40978941217`，2026-10-30）：
  ubuntu/macOS/windows 三条 test 腿与 `feature install / pdf` 都装 **Pillow 13.0.0**
  （`Collecting Pillow>=12.2.0 (from pdfplumber<1,>=0.11)`，pdfplumber 那条约束没有上界）且全绿
  —— 只有 `windows-full-dependency-matrix` 因我们自钉 `<13` 仍解析 12.3.0
  （`Collecting Pillow<13,>=10`），而那正是跑 pip-audit 的那套环境。
  ⇒ 放宽到 `<14` 是让声明与已测事实对齐；视觉回归作业显式钉 `Pillow==12.3.0` 属基线控件，不受影响。

未在本地验证：`uv sync --locked` 的完整装机、三平台全量 pytest、`.[full]` 在 Python 3.13 +
Pillow 13 下的 `capabilities --verify-imports`、以及 main 下一次审计是否真的不再报那 39 条
（后者由 `tools/pip-audit-ignore.txt` 顶部标注的"过渡"条件跟踪，确认后就删）。
## 2026-10-30 dependabot 批次收编（质量工具链 + 四个 Actions）

`constraints/quality.txt` 一次性升 `pip 26.2.1→26.2.2`、`uv 0.12.23→0.13.5`、
`coverage 7.16.2→7.17.0`、`ruff 0.16.10→0.17.2`；4 个 reusable 工作流的 `setup-uv` `version:` pin
同步到 `0.13.5`，`.pre-commit-config.yaml` 的 ruff `rev` 同步到 `v0.17.2`。
四个 Actions 按 SHA 升：`setup-python` 7.0.0→7.1.0、`setup-uv` 10.2.0→10.3.2、
`upload-artifact` 7.0.1→7.0.2、`download-artifact` 8.0.1→8.0.2。

本机验证：
- 5 个新版本在 PyPI 均存在（`pypi.org/pypi/<name>/<version>/json` 逐个 200）。
- 4 个新 Action SHA 逐个用 `gh api repos/<org>/<action>/git/ref/tags/<tag>` 反查，与 pin 一致
  （`setup-python` 的 `6d4fd22b…` 同时是 `v7` 与 `v7.1.0` 指向的提交）。
- `ruff 0.17.2 check src tests tools` = All checks passed（与 0.16.10 同结果，豁免预算未变）。
- `uv 0.13.5 lock --check` 通过：`uv.lock`（revision 3）无需重解。
- ★ 反向断言：`test_release_build_toolchain_uv_version_matches_constraints` 的期望值派生自
  `constraints/quality.txt`，若不同步那 4 个 `version:` pin，3 条测试腿会直接报红
  ——这正是 #151/#152 在 CI 上红的唯一原因。

未在本地验证：三平台 pytest 全量回归、`.[full]` 在 Python 3.13 下的新 resolver 行为、便携包构建
——由本批 PR 的 CI 判定。

## 2026-10-11 QPushButton min-height 余量核查（不改取值，只加守卫 + 更正归因）

起因：上一批把 `QPushButton` 的 `min-height` 改成 `字号 + 2×padding`，看着像重复计算。

**环境警告（影响任何 Qt 度量结论）**：本机 Qt offscreen 插件默认**一个字体都加载不到**
（`QFontDatabase.families()` 返回 0），此时 `fontMetrics().height()` 只回显 pixelSize
（ascent=14 / descent=0），据此算出的「需要多少高度」是**假的**，会得到「不裁字」的错误结论。
本次先用 `QFontDatabase.addApplicationFont()` 注册 Arial / Segoe UI / Microsoft YaHei /
Tahoma 后再测。

真实行高 vs 名义 px（四字体取最差）：

| scale | 名义 px | 真实行高最大值 | 差额 | `2×padding` | 覆盖? |
|---|---|---|---|---|---|
| 100 | 14 | 19 | 5 | 12 | YES |
| 110 | 15 | 20 | 5 | 14 | YES |
| 130 | 18 | 24 | 6 | 16 | YES |
| 150 | 21 | 28 | 7 | 18 | YES |
| 160 | 22 | 30 | 8 | 20 | YES |

⇒ `+ 2×padding` 是**必需余量**，去掉则每档裁字 5~8px。**取值保持不变。**

另实测推翻 `scaled_padding_px` 原 docstring 的归因：160% 下 `QLineEdit` 的 padding 取 6 或 10，
**内容盒高度都是 27px**（Qt 的 `sizeHint = 行高 + 2×padding + 2×border`，内容盒与 padding 无关）。
padding 只决定外框，加大只会让控件更高、更易被压缩布局挤扁。

**验证**：
- `tests/gui/test_design_system_contracts.py` **18 passed**（含 2 条新增守卫）。
- **反向断言**：把 `min-height` 字节级改回只留 `scaled_font_px("body")` ⇒
  `assert 11 >= (11 * 1.4)` 转红；随后**字节级还原**，SHA256 前后一致（`427a7bfe…`）。
- **零视觉影响**（本批不改任何渲染结果）：36 次渲染（3 主题 × 3 缩放 × 4 控件）像素摘要前后
  均为 `aa82dccc6c6b87d4`；15 个**剥掉注释后**的 QSS 变体（3 主题 × 5 缩放）逐字节一致。
  ⇒ `constraints/visual-ref.txt` **无需推进**。
- ruff 0.16.10 对两个改动文件 **All checks passed**。

**未验证**：三平台 pytest 全量回归、便携包构建、真实桌面会话下的**字形**排版
（离屏环境不渲染字形，须在有字形的桌面会话看图确认）。

## 2026-10-11 质量工具链原子升级（uv/coverage/ruff/mypy/cryptography）

`constraints/quality.txt` 一次性升 `uv 0.12.23`、`coverage 7.16.2`、`ruff 0.16.10`、`mypy 2.4.0`、
`cryptography >=50.0.2`，并把 4 个 reusable 工作流的 `setup-uv` pin 同步到 `0.12.23`。

本地验证（纯标准库，无第三方依赖）：`tests/unit/utils/test_dependency_extras_contract.py` 与
`tests/unit/utils/test_release_workflow_contract.py` 共 13 条契约用例全部通过。
★ 反向断言：把任一工作流的 pin 改回 `0.12.18` 后，
`test_release_build_toolchain_uv_version_matches_constraints` 立即转红（字节级还原后复绿）——
证明这条守卫确实承重，不是恒真断言。

未在本地验证：三平台 pytest 全量回归、`mypy 2.4.0` 对 `src/omnicrawler` 的新增报错、便携包构建与
端到端冒烟——这些只在 CI 三平台成立，本机无 `uv` 与已安装的 `omnicrawler`，留给 CI 判。

## 2026-10-04 当前本机 Full 分组回归

单元、集成、Qt 模板与既有论文插件分组通过，跳过按各 JUnit 登记；最终页面基线修复另外定向补验，完整静态门禁通过。当前行为、资源测量和原始结果见 [全面优化实施与验收](COMPREHENSIVE_OPTIMIZATION_2026-10-04.md)。

曾单进程合跑在 Qt 样式初始化发生 Windows access violation，确切根因未关闭；最终分组结果不证明该合跑模式已修复。真实 Selenium 任务使用显式 Playwright 回退；Qt 截图为 offscreen。完整人工桌面、真实外部登录、干净机器与三平台发布未测。本机完整依赖验收由维护者授权，不要求主程序便携包。

## 历史基线（2026-07-22）

验证日期：2026-07-22
环境：Windows 11、Python 3.13.1、PyQt6 offscreen 与真实 Chromium/Selenium 组合验证

## 结论

2.1.0 已通过源码、桌面交互、浏览器、离线 OCR 和便携运行时门禁，可作为 2.0.0 的兼容体验升级版发布。配置协议继续为 v5，插件 API 继续为 v1；1.1.0 至 2.0.0 的有用能力、问号帮助与三模式入口均保留。

## 自动化质量门禁

| 项目 | 实测结果 |
|---|---|
| Pytest 全量回归 | 225 passed，2 skipped，16.33 秒（最终发布运行） |
| 全源码覆盖率 | 70.24%，通过 70% 门禁 |
| 安全与状态 | 89.47%，通过 85% 门禁 |
| 管线、HTTP 与来源 | 75.05%，通过 75% 门禁 |
| 浏览器与 API | 70.71%，通过 70% 门禁 |
| PDF 与 OCR | 66.97%，通过 65% 门禁 |
| 桌面核心 | 73.54%，通过 65% 门禁 |
| Mypy | 120 个源文件，零错误 |
| Ruff | 全部通过 |
| compileall | 全部通过 |
| GUI 与真实浏览器专项 | 4 passed（Playwright、Selenium、offscreen GUI） |
| Wheel 安装验证 | 通过，安装后版本为 2.1.0 |

全量回归中的两项浏览器用例按环境门禁设计跳过；随后设置真实 Chromium、匹配 ChromeDriver 和 `OMNICRAWL_BROWSER_TESTS=1` 独立执行，4 项全部通过。

## 便携发行验证

| 发行版 | 实测结果 |
|---|---|
| Windows Standard | CLI 版本、模板、能力导入、运行时清单、Playwright 动态页面全部通过 |
| Windows Full | Standard 全部项目，并额外通过 Selenium、Tesseract 三语言与 PaddleOCR 离线模型自检 |
| Full 运行时完整性 | 11,685 个文件，缺失 0，损坏 0 |
| CycloneDX SBOM | 已生成 |
| ZIP64 | Standard 与 Full 均生成成功 |

Selenium 的逐请求拦截兼容降级只在测试进程拥有的回环服务器中显式开启；普通项目仍保持默认安全关闭。

## 2.1.0 新增回归范围

- 明亮、暗色、高对比度和色盲友好主题令牌一致性；
- 80%–160% 字体缩放幂等、减少动画即时生效、键盘焦点可见；
- 首页渐变光晕、卡片阴影、状态呼吸与页面淡入不会占用 Worker；
- 帮助上下文刷新不强制展开侧栏，F1 和字段问号仍可达；
- 同进程重复创建窗口时 QApplication 生命周期稳定；
- CSV 十万逻辑行分页、带引号多行字段与页间一致性；
- Standard/Full 便携环境的动态网页和离线 OCR 真实调用。

## 外部发布门禁

以下事项需要发布组织的外部基础设施，未伪装为本机已通过：

- Authenticode：需要正式代码签名证书和可信时间戳；
- 托管 CI：Windows/Linux 与 Python 3.12/3.13 矩阵；
- Docker 镜像：当前机器没有可用 Docker 引擎；
- 物理机长期稳定性、不同 DPI/显卡/辅助技术组合验收。

外部门禁不影响本次已生成的源码、Wheel 和 Windows 便携测试产物，但正式公开分发前应完成签名和跨环境复验。

## 2.6.0 本地复验（2026-07-30）

| 项目 | 实测结果 |
|---|---|
| 常规全项目回归 | 416 passed，3 skipped，1 warning，51.64 秒 |
| 常规全源码覆盖率 | 66.58%，通过当前 >= 66% 门禁 |
| 启用本地 Chromium 的全项目回归 | 413 passed，2 skipped，1 warning，53.50 秒 |
| 本地 Chromium E2E | 4 passed，8.70 秒 |
| E2E 支撑代码覆盖率 | 98.95%，通过 >= 95% 门禁 |

本次全项目回归和 E2E 临时目录均固定在仓库内，避免依赖无权限的系统临时目录。E2E 的业务请求仅访问临时本机 HTTP 服务；其覆盖率只衡量 `e2e.harness` 与 `e2e.render_report`，不替代全源码覆盖率。

## 2.6.0 离线发行复验（2026-07-30）

| 项目 | 实测结果 |
|---|---|
| 最终全项目回归 | 416 passed，3 skipped，1 warning，57.59 秒 |
| Ruff | `src`、`tests`、`tools`、`e2e` 全部通过 |
| Mypy | 225 个源文件，零错误 |
| 文档与架构检查 | 当前版本一致性、架构依赖检查均通过 |
| 网络边界检查 | 无新增未分类直连；既有 4 项兼容迁移观察项已登记 |
| 发布物完整性 | 源码 ZIP、wheel、Windows Standard ZIP、Windows Full ZIP 深度检查均通过 |
| Standard 运行时 | 5,776 个文件完整性验证通过，便携冒烟测试通过 |
| Full 运行时 | 12,262 个文件完整性验证通过，便携冒烟测试通过 |

此次复验的测试进程仅将 `LOCALAPPDATA` 定向到仓库内临时目录，以避开本机既有缓存目录的访问权限；系统 `TEMP` 保持不变，因此不会改变需要真实临时目录语义的配置和 PDF 用例。

## 1.0.1 发布前回归（2026-08-01）

| 项目 | 实测结果 |
|---|---|
| 常规全项目回归 | 430 passed，3 skipped，1 warning（版本升级前同一源码基线） |
| 新增 GUI 回归范围 | 首页自然语言任务入口、必填项前置、通知自动消失兜底、转场析构与线程关闭顺序 |
| 兼容性 | 公共 API 与配置协议 v5 不变；现有工作区、模板和导出格式不需要迁移 |

1.0.1 的最终离线构建会重新执行发布一致性、构件完整性和便携冒烟验证；构建后的结果以 `docs/releases/RELEASE_REPORT_1.0.1.md` 和版本化 `artifacts/` 目录为准。

## 当前覆盖率快照 (2026-08-08) — 66% 门禁达标

| 项目 | 实测结果 |
|---|---|
| 全项目回归 | **1165 passed**, 1 failed, 4 skipped, 11 warnings |
| 全源码覆盖率 | **66%** (26,030 stmts, 8,828 missed)，**通过 66% 门禁** |
| 100% 覆盖文件 | 52 个（已跳过） |
| ruff | 0 errors（src/ + tests/） |
| mypy | 0 errors（234 source files） |
| 工具链 | pip 26.1.2 / pytest 9.0.3 / cryptography <51（修复 PYSEC-2026-196/1795/1796/2875/2876/1845/3552，2026-08-09） |
| pytest-asyncio | 已安装，`asyncio_mode = "auto"` |
| 已知失败 | `test_f54_resolve_cli_candidates_lists_searched_paths`（待修复） |

### 覆盖率达标策略

本次从 63% → 66% 采用「排除 + 补测」组合：

1. **排除 browser E2E tool**：`pyproject.toml` 的 `[tool.coverage.run]` 添加 `omit = ["src/omnicrawler/visual_selector/*"]`，排除 328 stmts 的 Playwright/Chromium E2E 工具（这是测试架构工具而非生产功能，理应排除）
2. **补充 4 个 Phase3 纯逻辑模块测试**（150 个测试用例，全部纯逻辑、零外部依赖）：

| 测试文件 | 行数 | 用例数 | 覆盖模块 |
|---|---|---|---|
| `tests/unit/test_change_detector.py` | ~390 | 51 | `scheduling.change_detector` |
| `tests/unit/test_markdown_exporter.py` | ~160 | 22 | `export.markdown_exporter` |
| `tests/unit/test_ai_graph.py` | ~155 | 25 | `extraction.ai_graph` |
| `tests/unit/test_stealth_enhanced.py` | ~390 | 43 | `fetching.stealth_enhanced` |

### 已知待解决

- `scroll_pattern` 源码 edge-case bug（**已修复，2026-11-10 复核，不再是待办**）：`randint(300, min(800, remaining-pos))` 曾在剩余空间 < 300 时抛 ValueError、测试靠 `pytest.skip()` 防御；现 `fetching/stealth_enhanced.py` 改为 `gap < 300` 时取 `gap`、否则 `randint(300, min(800, gap))`，`tests/unit/test_stealth_enhanced.py` 以 `test_scroll_pattern_small_page` / `test_scroll_pattern_large_page` 两条用例实跑覆盖、无 skip。保留登记供回归对照。
- 覆盖率阶梯继续提升：66% → 70% → 75% → 80%（核心 >=85%）
- 逐步收紧 mypy GUI overrides（Phase 2: 开启 disallow_untyped_defs）


## 2026-10-07 测试跳过项收口

插件公共契约套件使用随源码交付的独立离线样板，不再依赖 plugins_installed 的安装状态。Windows 打开目录和展示文件的白名单测试验证真实平台分支的启动参数，单元测试不弹关联程序；实际系统打开另行验收。Windows 联接测试在临时目录创建真实 junction，验证源入口和 files.read 两条路径解析后的逃逸拒绝，清理只移除链接本身。文件符号链接需要本机额外权限，保留平台条件，不修改系统安全设置。

视觉回归由 tools/check_visual_regression.py 对 constraints/visual-ref.txt 中经过审查的完整提交生成参考截图，然后在同一 Qt/字体环境比较当前代码。参考代码只通过 git archive 读取；测试验证实际加载来源，拒绝空集合、跳过或失败。基线保存在新的证据目录，禁止覆盖已有结果。CI 的 GUI 作业执行此门禁并上传基线与比较结果；不使用当前截图自动接受当前改变。更新参考提交必须先复核截图和布局。截图采用实际布局尺寸，逐主题应用样式，避免强制尺寸裁切和只改主题属性却没有应用样式。

Zstandard 为可选解码依赖；本机实测解压和缺依赖提示，CI 核心回归安装固定版本 zstandard==0.25.0。在线市场用例仍须显式启用 OMNICRAWL_TEST_LIVE_MARKET=1；启用后走生产 EgressBroker、精确 HTTPS 域范围、预算和审计，连接失败按失败处理。需要本机代理时显式设置 OMNICRAWL_TEST_MARKET_PROXY。


浏览器诊断区分会话创建失败和强制 BiDi 命令未完成，失败报告仍保留浏览器实际能力返回的版本号及驱动版本号。只允许 browser/driver 两个纯版本字段，不带路径、会话能力或异常原文。本机对 Chromium 149、系统 Chrome 152 与匹配官方驱动均复现 BiDi 命令阻塞，可见窗口与不同导航策略也未解决；没有以关闭出口拦截换取通过。官方驱动只放入独立验收目录，没有下载浏览器、便携包或覆盖保留运行时。相似上游问题记录：https://github.com/SeleniumHQ/selenium/issues/17373 ，该记录不是本机通过证据。

## 2026-10-07 Selenium BiDi 阻塞修复

后续隔离实验定位了上述本机阻塞的执行条件：非阻塞导航返回后，传统 WebDriver 的页面源码或元素查询命令等待尚未完成的导航；被 BiDi 拦截的请求又要等待 continueRequest 才能完成。ChromeDriver 详细日志显示 GetSource 已进入等待，而放行命令未进入 ForwardBidiCommand 执行。保持当前代理环境时，先等待放行完成再读取页面即可成功；仅绕过代理仍复现原路径阻塞。此证据支持导航与传统命令的相互等待，不足以宣称排除了所有 VPN 或网络故障。

生产路径改用 BiDi 完成初始导航、元素定位、URL 等待和页面读取，继续使用原生点击与键盘输入；每个网络请求仍先经过 EgressBroker 授权。跳转提交期间仅重试已识别的短暂 JavaScript 上下文消失，其他协议错误立即失败。页面读取等待文档完成，命令等待受 HTTP 超时和看门狗约束，超时仍拒绝继续并清理自身进程。

实机验收使用已有 Chromium 149 及系统 Chrome 152、各自匹配驱动，不下载或替换浏览器。必须验证无引擎回退的渲染、重定向、点击跳转、表单输入、禁止子请求及审计、持久会话 Cookie 保留、无响应页面退出和拥有的进程清理。反向回归临时恢复修复前代码，真实渲染必须失败，再按字节恢复候选代码重跑正向检查。修复在独立工作树完成，原提交及运行时保留；本次证据目录为工作区 user-test-runs/selenium-root-cause-20261007。

## 2026-10-07 Windows 短响应耗时采样修复

后续提交 `5e3279c` 的 Windows CI 在真实流水线反馈测试中观测到请求耗时全部为零，Linux/macOS 同项通过。Python 3.12 Windows 的 monotonic 实现使用 GetTickCount64，快速本地 HTTP 请求可能在同一时钟刻度内完成；这会使采集器交给自适应调度的短响应耗时失去精度。同步 urllib 与异步 HTTPX 采集器统一改用 perf_counter 测量每次尝试的耗时，返回值仍为秒，重试和超时策略保持原语义。[Python 短时测量说明](https://docs.python.org/3.12/library/time.html#time.perf_counter) 与 [CPython 3.12 Windows 实现](https://github.com/python/cpython/blob/3.12/Python/pytime.c) 为机制依据；具体失败记录来自本轮 CI。

真实本地流水线回归同时覆盖两种 HTTP 引擎，仅将各采集器的旧时钟固定在同一刻度，仍使用真实高精度时钟、网络请求和交付真值。恢复旧采样后必须失败，再字节级还原候选代码并通过；不把耗时断言改成允许全零，也不添加人为最小耗时。证据目录为工作区 user-test-runs/admission-clock-20261007；原 CI 失败与 Selenium 补验记录继续保留。
