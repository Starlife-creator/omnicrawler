"""MainWindow 组合根契约（`docs/MAINWINDOW_REFACTOR.md` §3 第①条）。

这些用例是**物理拆分的前置安全网**：它们只断言「窗口最终长成什么结构」，
不断言「由哪个方法/哪个 delegate 拼出来」。因此把 `_setup_central_area` 的
装配代码整块搬进 delegate、或把 `__init__` 拆成若干段，只要最终结构不变，
本文件必须仍然全绿；反之若搬迁漏接一条信号、少挂一个页面、导航映射错位，
这里立刻红。

刻意不做的事：
- 不 import 被搬迁的私有方法名（`_setup_central_area` 等），否则拆分即红；
- 不触发会弹窗的菜单动作，只用 ``receivers()`` 断言「有接线」；
- 不依赖动画时长，只断言 ``QStackedWidget.currentIndex``。
"""

from __future__ import annotations

import importlib.util
import inspect

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="MainWindow contract test requires PySide6",
)

# 反向断言用例（`test_wiring_detector_*` / `test_navigation_map_check_*`）不取
# `main_window` fixture，却要自己造 QWidget。**必须**保证 QApplication 已存在且
# 被本模块强引用：
# - 不存在时 Qt 直接 qFatal（`QWidget: Must construct a QApplication before a
#   QWidget`，Windows 上进程以 0xC0000409 中止），单跑/指定用例跑必崩；
# - 只存局部变量时会被 GC 连带销毁控件树（同 `test_field_value_source_columns.py`
#   的注释），pytest 退出期还会踩 native access violation。
# 全量跑时通常被前面的 GUI 用例“顺带”建好 app，所以该缺陷只在子集运行时暴露。
_APP = None


def _ensure_app():  # type: ignore[no-untyped-def]
    """返回进程内唯一的 QApplication，并以模块级引用持有它。"""
    global _APP

    from PySide6.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP

# 导航行号 → 页面栈索引。取自 `main.py` 的 nav_items 顺序；这是**产品结构**，
# 不是实现细节，拆分前后都必须一致（`navigation.NavIndex` 亦按此顺序定义）。
_EXPECTED_NAV_PAGES = {
    1: 4,   # 首页
    2: 0,   # 任务工作台
    3: 2,   # 运行与历史
    4: 3,   # 结果与复核
    6: 8,   # 变更监控
    8: 6,   # PDF 工作台
    9: 7,   # 格式互转
    10: 11,  # 场景与模板
    11: 12,  # 登录会话
    12: 13,  # 市场橱窗（QML 试点）
    14: 1,  # YAML 编辑器
    15: 5,  # 证据查看器
    16: 9,  # 插件市场
    17: 10,  # 开发者检查器
}

# 页面栈里的页面必须能被主窗口按名字持有（拆分后仍须如此，否则关闭期清理会漏）。
_EXPECTED_PAGE_OWNERS = (
    "_home",
    "_workspace_widget",
    "_yaml_editor",
    "_evidence_view",
    "_pdf_workbench",
    "_convert_tool",
    "_change_monitor",
    "_plugin_market",
    "_developer_inspector",
    "_scene_panel",
    "_login_session_view",
    "_qml_showcase",
)


@pytest.fixture(autouse=True)
def _chinese_ui() -> None:
    """文案断言与语言切换用例解耦（同 `test_gui_smoke.py` 的做法）。"""
    from omnicrawler.gui import i18n

    i18n.set_language("zh_CN")


@pytest.fixture
def main_window(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from omnicrawler.gui.main import MainWindow
    from omnicrawler.gui.views.login_session import LoginSessionView

    # 首启欢迎是 100ms 后的定时弹窗，offscreen 下会打断后续断言。
    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    # 首次进「登录会话」页会弹一次模态 `QMessageBox.information`
    # （`login_session.py:577 _show_first_run_notice`）。本文件要逐行导航到
    # **每个**页面，走到 LOGIN_SESSION 时没人点「确定」，offscreen 下就是永久
    # 阻塞——CI 上表现为 30/45 分钟零输出后 cancelled（2026-11-11 用
    # `-o faulthandler_timeout=300` 取到栈，顶层是 login_session.py:579）。
    # 本文件只验证「导航行 → 页面栈索引」映射，不验证首次提示文案；
    # 提示本身的判据在 `tests/unit/gui/test_login_session_view.py`。
    monkeypatch.setattr(LoginSessionView, "_show_first_run_notice", lambda self: None)
    app = _ensure_app()
    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def _menu_actions(window):  # type: ignore[no-untyped-def]
    """菜单栏里所有「可点」的动作（不含分隔符与子菜单容器）。"""
    actions = []
    for top in window.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        for action in menu.actions():
            if action.menu() is not None or action.isSeparator():
                continue
            actions.append(action)
    return actions


def _is_wired(action) -> bool:  # type: ignore[no-untyped-def]
    """动作是否接了自己的槽。

    ``QAction.receivers()`` 的基线不是 0：菜单自身会占一个 ``triggered`` 接收者，
    所以「没人接」时 ``triggered`` 读数为 1。实测三种状态：
    未接线 → triggered=1/toggled=0；``triggered.connect`` → 2/0；
    ``toggled.connect``（可勾选项） → 1/1。
    """
    return (
        action.receivers("2triggered(bool)") >= 2
        or action.receivers("2toggled(bool)") >= 1
    )


def test_window_starts_with_navigation_and_page_stack(main_window) -> None:  # type: ignore[no-untyped-def]
    """启动即完成中央区域装配：左侧导航 + 右侧页面栈，且都挂在中央控件上。"""
    from PySide6.QtWidgets import QListWidget, QStackedWidget

    central = main_window.centralWidget()
    assert central is not None, "中央控件缺失——装配未完成"
    assert isinstance(main_window._nav, QListWidget)
    assert isinstance(main_window._stack, QStackedWidget)
    assert central.isAncestorOf(main_window._nav)
    assert central.isAncestorOf(main_window._stack)

    # 18 行 = 14 个页面项 + 4 个分组标题；页面栈 14 页。
    assert main_window._nav.count() == 18
    assert main_window._stack.count() == 14
    assert main_window._nav.objectName() == "mainNavigation"
    assert main_window._nav.width() == 190


def test_navigation_map_points_at_real_pages(main_window) -> None:  # type: ignore[no-untyped-def]
    """导航行号→页面索引的映射完整、无悬空、无重复指向。"""
    assert main_window._nav_pages == _EXPECTED_NAV_PAGES

    for row, page_index in main_window._nav_pages.items():
        widget = main_window._stack.widget(page_index)
        assert widget is not None, f"导航第 {row} 行指向不存在的页面 {page_index}"

    # 每个页面都被某个导航项引用（不允许有「造了但进不去」的孤儿页）。
    assert set(main_window._nav_pages.values()) == set(range(main_window._stack.count()))


def test_group_headers_are_not_selectable(main_window) -> None:  # type: ignore[no-untyped-def]
    """分组标题（工作/自动化/工具/高级）不可选中，否则会跳到上一页造成错页。"""
    from PySide6.QtCore import Qt

    from omnicrawler.gui.navigation import NavIndex

    for row in NavIndex.HEADERS:
        item = main_window._nav.item(row)
        assert item.flags() == Qt.ItemFlag.NoItemFlags, f"第 {row} 行分组标题可选中"


def test_pages_are_owned_by_the_window(main_window) -> None:  # type: ignore[no-untyped-def]
    """每个页面都必须由主窗口按名字持有（关闭期清理与信号协调依赖这些引用）。"""
    for attr in _EXPECTED_PAGE_OWNERS:
        page = getattr(main_window, attr, None)
        assert page is not None, f"页面 {attr} 未挂在主窗口上"
        assert main_window._stack.indexOf(page) >= 0, f"{attr} 不在页面栈里"


def test_every_menu_action_is_wired(main_window) -> None:  # type: ignore[no-untyped-def]
    """菜单动作必须真的接到东西。

    删掉 `_setup_menu_bar` 这类纯转发壳时最容易出的错，是菜单建了但 connect
    漏了——点击无反应且无报错。判据见 ``_is_wired``：``receivers()`` 的
    ``triggered`` 基线是 1（菜单自身占用），不是 0。
    """
    menubar = main_window.menuBar()
    assert menubar is not None
    titles = [action.text() for action in menubar.actions()]
    assert len(titles) == 3, f"菜单栏顶层项数变了：{titles}"
    for expected in ("文件", "设置", "帮助"):
        assert any(expected in title for title in titles), f"缺少「{expected}」菜单"

    actions = _menu_actions(main_window)
    assert len(actions) >= 25, f"可点菜单动作过少（{len(actions)}），疑似装配漏项"
    unwired = [action.text() for action in actions if not _is_wired(action)]
    assert not unwired, f"这些菜单动作没有接线：{unwired}"


def test_navigation_switches_every_page(main_window) -> None:  # type: ignore[no-untyped-def]
    """逐行导航到每个页面，页面栈必须切到映射索引。"""
    for row, page_index in main_window._nav_pages.items():
        main_window._nav.setCurrentRow(row)
        assert main_window._stack.currentIndex() == page_index, (
            f"导航第 {row} 行未切到页面 {page_index}"
        )

    # 分组标题不改变当前页。
    from omnicrawler.gui.navigation import NavIndex

    main_window._nav.setCurrentRow(NavIndex.WORKSPACE)
    before = main_window._stack.currentIndex()
    main_window._nav.setCurrentRow(NavIndex.AUTOMATION_HEADER)
    assert main_window._stack.currentIndex() == before


def test_first_run_notice_guard_is_real_not_a_silent_noop(main_window) -> None:  # type: ignore[no-untyped-def]
    """反向断言：`_show_first_run_notice` 的替换必须真的生效。

    取 `main_window` fixture 是**故意的**：fixture 执行后 patch 才在类上生效，
    本用例断言的就是「此刻类属性已被替换」。若 fixture 里那行 monkeypatch 被
    删掉或方法改名，`test_navigation_switches_every_page` 在「全新环境」（CI
    runner 从无 `session.notice_ack_v1`）会退回成永久阻塞，本用例当场判红。

    这里刻意**不**断言「调用后没弹窗」——那取决于 `session.notice_ack_v1` 的
    本机状态（本机手工运行过 GUI，该键已是 True，`_needs_notice()` 返回 False，
    真假守卫都表现为不弹窗，判据会被环境掩盖）。
    """
    from omnicrawler.gui.views.login_session import LoginSessionView

    # 前提：产品侧这个提示是阻塞式模态调用，不是普通 setText。
    # 注意从**类源码**里取，不能从模块属性取：fixture 已把类上的该方法换成
    # no-op lambda，模块上也没有这个属性（它是 `LoginSessionView` 的方法）。
    class_source = inspect.getsource(LoginSessionView)
    assert "def _show_first_run_notice" in class_source, (
        "登录会话首次提示的方法名变了，`main_window` fixture 的 monkeypatch 会静默失效"
    )
    notice_body = class_source.split("def _show_first_run_notice", 1)[1]
    assert "QMessageBox.information" in notice_body.split("\n    def ", 1)[0], (
        "登录会话首次提示不再是模态调用，本守卫的前提失效，需要重新评估阻塞风险"
    )

    # 判据：替换必须生效（方法改名/漏 patch 时这里就是原名函数）。
    assert getattr(LoginSessionView._show_first_run_notice, "__name__", "") != (
        "_show_first_run_notice"
    ), (
        "main_window fixture 未替换 `_show_first_run_notice`："
        "全新环境下导航到登录会话页会弹模态框并永久阻塞"
    )


def test_close_runs_full_teardown(main_window, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """关闭生命周期完整：各子系统 shutdown 都被调用，窗口最终不可见。"""
    calls: list[str] = []
    for owner, method in (
        (main_window._autosave, "stop"),
        (main_window._home, "shutdown"),
        (main_window._change_monitor, "shutdown"),
        (main_window._login_session, "shutdown"),
    ):
        monkeypatch.setattr(owner, method, lambda name=method: calls.append(name))

    main_window.show()
    assert main_window.isVisible()
    main_window.close()

    assert "stop" in calls, "关闭时未停止自动保存"
    assert "shutdown" in calls, "关闭时未停子系统"
    assert not main_window.isVisible()


def test_background_threads_hook_is_consulted_on_close(main_window) -> None:  # type: ignore[no-untyped-def]
    """关闭前必须查询后台线程列表（拆分后这个协调点不能被搬丢）。"""
    assert isinstance(main_window._background_threads(), list)
    assert main_window._close_after_background_jobs is False


def test_wiring_detector_rejects_a_deliberately_dead_action() -> None:
    """反向断言：本文件的接线判据必须能抓到「建了但没连」的动作。

    没有这一条，``_is_wired`` 一旦写错（例如按 ``== 0`` 判断，而
    ``triggered`` 基线其实是 1），`test_every_menu_action_is_wired` 会永远
    通过，整个安全网就是装饰。
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMainWindow

    _ensure_app()
    window = QMainWindow()
    menu = window.menuBar()
    assert menu is not None
    menu = menu.addMenu("probe")
    assert menu is not None

    dead = QAction("dead", window)
    menu.addAction(dead)
    alive = QAction("alive", window)
    alive.triggered.connect(lambda: None)
    menu.addAction(alive)
    toggled = QAction("toggled", window)
    toggled.setCheckable(True)
    toggled.toggled.connect(lambda _checked: None)
    menu.addAction(toggled)

    assert not _is_wired(dead), "未接线的动作被判成了已接线——判据失效"
    assert _is_wired(alive)
    assert _is_wired(toggled)
    window.deleteLater()


def test_navigation_map_check_rejects_a_dangling_row() -> None:
    """反向断言：映射校验必须能抓到「导航行指向不存在的页面」。"""
    from PySide6.QtWidgets import QStackedWidget, QWidget

    _ensure_app()
    stack = QStackedWidget()
    # 真实页面栈有 14 页；这里照建，才能确认「唯一悬空的是我伪造的那行」。
    for _ in range(max(_EXPECTED_NAV_PAGES.values()) + 1):
        stack.addWidget(QWidget())

    mapping = dict(_EXPECTED_NAV_PAGES)
    mapping[99] = 42  # 超出页面栈范围
    dangling = [row for row, page in mapping.items() if stack.widget(page) is None]
    assert dangling == [99], f"悬空行未被识别：{dangling}"
    stack.deleteLater()
