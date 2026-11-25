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
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("PYTHONHASHSEED", "42")


#: **测试用的数据根**：整轮会话落在系统临时目录，绝不碰真实用户目录。
#:
#: 为什么需要（2026-11-25 实测）：`tests/unit/utils/test_build_runtime_fixes.py`
#: 的 `test_f28` 会 `monkeypatch.setattr(runtime_paths, "portable_data_root", ...)`
#: 并 `cache_clear()`；同进程接着跑 `test_f39` 时，`portable_data_root()` 重新按
#: 默认规则解析 —— 当时仓库里没有 `PORTABLE.flag`，于是落到**真实的**
#: `%LOCALAPPDATA%\\OmniCrawler` 并 `mkdir` 出一个空目录。
#:
#: 后果不只是「多一个空目录」：`check_test_cleanliness.py` 判的是 git 状态，
#: 那个目录在用户家目录、不在仓库里，**看不出**；而用户下次启动应用会看到
#: 一个来历不明的数据目录。开发机因此从"便携"退化成"在 C 盘留垃圾"。
#:
#: 修法：把数据根判定链上的两个输入都指向会话临时目录 ——
#:
#: * `OMNICRAWL_INSTALL_ROOT` ⇒ `install_root()`（首选根）；
#: * `LOCALAPPDATA` / `HOME` ⇒ 兜底根。
#:
#: 两者都指向临时目录后，无论走哪一条分支都落在临时目录内。
#: 需要在用例里改这些变量的测试仍可用 `monkeypatch.setenv` 覆盖（fixture
#: 与模块级设置都只是 `setdefault` 语义的"会话初值"）。
_TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="omnicrawler-test-data-"))
os.environ["OMNICRAWL_INSTALL_ROOT"] = str(_TEST_DATA_ROOT)
os.environ["LOCALAPPDATA"] = str(_TEST_DATA_ROOT / "LocalAppData")
os.environ["XDG_DATA_HOME"] = str(_TEST_DATA_ROOT / "Xdghome")
#: Windows 缓存路径常被写成 `%SystemDrive%\\ProgramData\\...`；缺这个变量时
#: 它会被当成**相对路径**，于是在 cwd（仓库根）下生成一个字面名为
#: `%SystemDrive%` 的目录。`win_shortcut` 的文档里记过这个坑，这里一并堵住。
os.environ.setdefault("SystemDrive", "C:")
for _sub in ("LocalAppData", "Xdghome"):
    (_TEST_DATA_ROOT / _sub).mkdir(parents=True, exist_ok=True)

# **统一强制 offscreen**：22 个测试文件里各写一遍 `qapp`/`qt_app`/`_qt_app`，
# 但只有 5 处设了 `QT_QPA_PLATFORM=offscreen`，其余靠 CI 的全局环境变量兜着
# —— **本地缺该变量时会真的弹窗**（2026-11-23 实测 17 处缺这一步）。
# 在 conftest 的模块级设置，一处覆盖全部，且早于任何 Qt import。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


#: **统一的 QApplication fixture**。
#:
#: 此前 `qapp` / `qt_app` / `_qt_app` 在 22 个测试文件里各写一遍，实现细节
#: 互不一致：只有 5 处设了 `QT_QPA_PLATFORM=offscreen`，其余靠 CI 的全局
#: 环境变量兜着 —— **本地缺该变量时会真的弹窗**。2026-11-23 实测：
#: 17 处 fixture 缺这一步设置。
#:
#: 这里统一：session 级复用（Qt 只允许一个 QApplication）、强制 offscreen、
#: 并把语言重置为 zh_CN（GUI 断言依赖中文文案；`_restore_i18n_language`
#: 是逐测试兜底，这里是进入 Qt 前的初值）。
@pytest.fixture(scope="session")
def qapp():
    """整个会话复用同一个 offscreen QApplication。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip(
        "PySide6.QtWidgets", reason="需要 PySide6（gui extra）"
    )
    app = widgets.QApplication.instance() or widgets.QApplication([])
    return app


def _dispose_qt_test_widgets(app, widgets=None) -> None:
    """Dispose leaked test windows while their QApplication still owns Qt.

    逐用例强制派发 DeferredDelete 是「下一个用例改全局主题前必须真的销毁」这条
    契约的一部分（`tests/unit/gui/test_qt_session_cleanup.py` 守住它），不能为了
    绕开崩溃而省略：省略会留下仍 valid 的窗口，语义上是错的。coverage 跟踪下由此
    引发的解释器最终化段错误改由 `pytest_sessionfinish` 跳过最终化来规避。
    """
    from PySide6.QtCore import QCoreApplication, QEvent, QThread
    from shiboken6 import isValid

    widgets = list(app.topLevelWidgets()) if widgets is None else widgets
    for widget in widgets:
        if not isValid(widget):
            continue
        shutdown = getattr(widget, "shutdown", None)
        if callable(shutdown):
            shutdown()
        request_shutdown = getattr(widget, "_request_background_shutdown", None)
        if callable(request_shutdown):
            request_shutdown()
        threads = widget.findChildren(QThread)
        for thread in threads:
            if thread.isRunning():
                thread.requestInterruption()
                thread.quit()
                thread.wait(5000)
        assert not any(thread.isRunning() for thread in threads), (
            f"Running Qt thread remains in {type(widget).__name__}"
        )
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def _owned_qt_test_widgets(_owned_qt_test_session, monkeypatch):
    """Dispose this test's windows before another test changes the global theme."""
    qt_widgets = sys.modules.get("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() if qt_widgets is not None else None
    if app is None:
        yield
        return
    previous = set(app.topLevelWidgets())
    yield
    _dispose_qt_test_widgets(app, list(set(app.topLevelWidgets()) - previous))


@pytest.fixture(scope="session", autouse=True)
def _owned_qt_test_session():
    # Collection already imported GUI modules if this run needs Qt. Do not add a
    # Qt dependency to the minimal/core test environment.
    qt_widgets = sys.modules.get("PySide6.QtWidgets")
    if qt_widgets is None:
        yield
        return
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    yield
    _dispose_qt_test_widgets(app)
    # Python wrappers may have reference cycles. Collect before releasing our
    # application owner, rather than letting interpreter shutdown choose order.
    import gc
    gc.collect()


@pytest.fixture(autouse=True)
def _isolated_secret_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SecretsStore 缺省路径隔离（U5 起 session 链路会在测试中触达默认密钥库）。

    ★ 若不隔离：测试会读/写**真实**的 ``~/.omnicrawler/secrets.bin``，且在
    keyring 不可用的 CI runner 上直接 SecretsStoreError（U5 首轮 CI 三平台
    test 矩阵红光的根因）。只设路径与主口令，**不动** OMNICRAWL_KEYRING_DISABLE
    —— 该变量会覆盖注入的 keyring_api，破坏既有密钥测试；CI 上 keyring
    不可用时 SecretsStore 自行走口令派生兜底。个别测试需要别的环境时，
    在用例内 monkeypatch.setenv/delenv 即可覆盖本 fixture。
    """
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "test-only-master-password")
    monkeypatch.setenv("OMNICRAWL_TEMPLATE_CHECKS_PATH", str(tmp_path / "template_checks.sqlite3"))


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
