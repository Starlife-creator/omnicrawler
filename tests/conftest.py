"""测试套件共享 fixtures（P0-1 根因修复 + P1-14 sys.path 加成）。

- 固定 PYTHONHASHSEED：CI runner 熵不足时，被 spawn 的子进程（含
  IsolatedPluginRunner 沙箱）Python 解释器哈希随机化初始化偶发失败
  （_Py_HashRandomization_Init）。显式设种子可让 Python 跳过 OS 熵读取，
  行为完全确定。setdefault 尊重 CI/开发者已有的显式设置。
- sys.path 加入仓库根：多处测试 `from tools.* import` 依赖 repo_root
  在 sys.path。`python -m pytest` 时 cwd 在 sys.path 所以恰好可用；
  裸 `pytest` 时缺失会导致 collection error（P1-14）。
"""
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("PYTHONHASHSEED", "42")


@pytest.fixture(autouse=True)
def _sys_path_snapshot():
    """B13-004：每个测试结束后还原 sys.path。

    部分测试在模块级 `sys.path.insert(0, .../src)` 加载构建工具（build_runtime /
    cli_pipeline / cross_platform fixes）。该模块级 insert 是一次性全局副作用
    （无 exec/注入，仅为 `from tools.*` 可复现构建断言），fixture 无法 undo 它；
    此快照至少保证测试运行过程中任何动态 insert 不跨测试累积，污染后续
    测试的 import 解析顺序。
    """
    snapshot = list(sys.path)
    yield
    sys.path[:] = snapshot


@pytest.fixture(autouse=True)
def _reap_leaked_gui_widgets():
    """回收每个用例遗留的顶层控件——否则**后跑的** GUI 用例会被历史累积拖死。

    ## 为什么（实测，2026-09-22）

    `tests/gui/test_design_system_contracts.py::test_all_theme_variants_pass_strict_hex_guard`
    - 单独跑：0.05s；只跑本文件 16 个用例：0.22s（整文件）
    - 放进整套：**51.8s**（CI 上同用例 44.9s）

    `test_monitor_icon_changes_with_the_theme` 31.9s(CI)/48.9s(本地) → 单独跑 < 1s；
    `test_visual_theme_home_transition_and_help_visibility` 2.2s（只跑两个文件时）。

    机制：仅跑 2 个 GUI 文件后，进程里已有 **1016 个存活控件**（从未销毁）。
    而 `ThemeManager.apply()` 每次都会 `setStyleSheet` / `setPalette` / 改 app 字体，
    Qt 因此要对**所有存活控件**重算样式（含布局失效与字体解析）。
    实测（`bench_apply_inflated.py`，直接造 MainWindow 再计时）：

    | 存活 MainWindow | 存活控件 | 一次 apply() |
    | --- | --- | --- |
    | 0 | 0 | 0.0ms |
    | 1 | 1020 | 299ms |
    | 5 | 5116 | 2543ms |
    | 10 | 10236 | **10079ms** |

    ★ 只 `hide()` 无效（14628ms，控件仍在树里）——**必须真正销毁**，销毁后回到 0.1ms。

    ## 安全护栏（为什么不是无脑 `deleteLater()`）

    销毁 QObject 有两类**未定义行为**，踩到会 SIGSEGV/abort，且**只在部分平台暴露**：
    最初的无护卫版本本地（windows）整套 3602 passed 全绿，却在 ubuntu CI 的
    `gui-and-browser` 作业**测试全绿后于解释器退出阶段段错误**（exit 139）。
    故这里跳过：

    ① `widget.thread()` 不是当前线程的（跨线程删除 QObject 是未定义行为）；
    ② 子树里存在**正在运行**的 `QThread` 的（销毁会连带销毁它）。

    被跳过的数量会打到 stderr（「跳过必须可见」），因为它们仍会计入后续 `apply()` 的成本。

    ## 边界

    只回收 QWidget（`topLevelWidgets()` 不含 `QApplication` 本身），
    且只在 PySide6 已被导入时才动手，非 GUI 用例零成本。
    本改动不放宽任何断言：若某个用例原本依赖"上一用例的窗口还活着"，
    修的是那个用例的前置条件，不是把这里的回收去掉。
    """
    yield
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent, QThread
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return

    # 安全护栏：销毁 QObject 有两类未定义行为，踩到会 SIGSEGV/abort，
    # 且**只在部分平台暴露**（实测 windows 本地过、ubuntu CI 段错误）。
    current = QThread.currentThread()
    doomed = []
    kept = 0
    for widget in list(app.topLevelWidgets()):
        try:
            if widget.thread() is not current:
                kept += 1  # ① 跨线程删除 QObject 是未定义行为
                continue
            threads = widget.findChildren(QThread)
            if any(thread.isRunning() for thread in threads):
                kept += 1  # ② 销毁会连带销毁在跑的线程（Qt 会 abort / 踩内存）
                continue
        except RuntimeError:  # C++ 侧已被前序清理销毁
            continue
        doomed.append(widget)

    for widget in doomed:
        try:
            widget.hide()
            widget.deleteLater()
        except RuntimeError:
            pass

    # 只派发**我们自己排的**删除，避免动到其它库（浏览器驱动等）投递的 DeferredDelete
    for widget in doomed:
        try:
            QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
        except RuntimeError:
            pass

    if kept:
        # 「跳过必须可见」：这些控件会继续计入后续 apply() 的成本
        print(
            f"[conftest] 未回收 {kept} 个顶层控件（跨线程或含运行中的 QThread）",
            file=sys.stderr,
        )


@pytest.fixture(autouse=True)
def _restore_i18n_language():
    """隔离 i18n 的全局语言状态（它是进程级量，不还原就会跨测试泄漏）。

    实测：`tests/unit/gui/test_i18n_gate.py` 会调用 `set_language("en_US")` 而不还原，
    于是同一进程里后跑的 `tests/integration/template/test_simple_experience.py`
    断言中文文案时失败——测试结果**取决于先跑了哪些目录**。
    这里按测试快照并还原，让套件对运行顺序不敏感。
    """
    try:
        from omnicrawler import i18n
    except ImportError:  # pragma: no cover - i18n 不可用时不影响其它测试
        yield
        return
    previous = i18n.get_current_language()
    try:
        yield
    finally:
        if isinstance(previous, str) and previous:
            i18n.set_language(previous)
