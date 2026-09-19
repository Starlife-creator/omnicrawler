"""I2：欢迎弹窗上那个"创建桌面快捷方式"复选框的行为（离屏）。

三个方向都要断言：

* **默认不勾** ⇒ 走完欢迎弹窗**不创建任何东西**（反向断言："没做"也要被断言到）
* **勾了** ⇒ 创建函数被**恰好调用一次**
* **非 Windows** ⇒ 复选框**不存在**（断言控件缺席，而不是"出现了但点了没用"）

★ 模态 ``QMessageBox`` 必须先被替换成非阻塞实现 —— 否则离屏跑会**挂死而不是失败**
（红线）。这里用 monkeypatch 替换 ``QMessageBox.exec``，并在其中按 objectName 找到
那个复选框、模拟用户点击主按钮。

★ 按 objectName 找而不是按类型找：欢迎弹窗上**本来就有一个** QCheckBox（"不再显示"），
`findChildren(QCheckBox)` 会同时拿到两个。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QWidget

from omnicrawler.core import win_shortcut
from omnicrawler.gui.delegates.env_checker import SHORTCUT_CHECKBOX_NAME, EnvironmentChecker
from omnicrawler.gui.widgets import toast as toast_module

REPO_ROOT = Path(__file__).resolve().parents[3]
MENU_SOURCE = REPO_ROOT / "src" / "omnicrawler" / "gui" / "delegates" / "menu.py"


@pytest.fixture(autouse=True)
def _qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _StubNav:
    def setCurrentRow(self, *_args: Any) -> None:
        """不需要真的切页。"""


class _StubCanvas:
    def restart(self) -> None:
        """不需要真的重置画布。"""

    def focus_url_input(self) -> None:
        """不需要真的抢焦点。"""


class _StubToast:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def success(self, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)

    def warning(self, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)

    def info(self, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)


def _make_checker() -> EnvironmentChecker:
    """最小 stub 当 `_mw`：`show_welcome_dialog` 只用到 `_nav` 与 `_task_canvas`。"""
    parent: Any = QWidget()
    parent._nav = _StubNav()
    parent._task_canvas = _StubCanvas()
    return EnvironmentChecker(parent)


def _drive_welcome_dialog(
    monkeypatch: pytest.MonkeyPatch, *, check_shortcut: bool
) -> list[int]:
    """跑一遍欢迎弹窗；返回创建函数每次被调用的记录（长度＝调用次数）。"""
    calls: list[int] = []

    def fake_create(*_args: Any, **_kwargs: Any) -> win_shortcut.ShortcutResult:
        calls.append(1)
        return win_shortcut.ShortcutResult(ok=True, status="created")

    monkeypatch.setattr(win_shortcut, "create_shortcut_for_app", fake_create)
    # Toast 会建真控件；单测里替换掉，避免 GUI 副作用干扰断言
    monkeypatch.setattr(toast_module.ToastManager, "instance", staticmethod(lambda: _StubToast()))

    def fake_exec(box: QMessageBox) -> int:
        checkbox = box.findChild(QCheckBox, SHORTCUT_CHECKBOX_NAME)
        if checkbox is not None and check_shortcut:
            checkbox.setChecked(True)
        # 模拟用户点了主按钮（AcceptRole）。只勾选然后按右上角 X 不算同意 ——
        # 那时 clickedButton() 为 None，实现里刻意不建。
        for button in box.buttons():
            if box.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole:
                button.click()
                break
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    _make_checker().show_welcome_dialog()
    return calls


def test_default_unchecked_creates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 反向断言：默认不勾 ⇒ 一次都不许调用创建函数。"""
    calls = _drive_welcome_dialog(monkeypatch, check_shortcut=False)
    assert calls == [], "默认不勾时不得创建快捷方式"


def test_checked_creates_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """勾了 ⇒ 恰好一次（不多不少）。"""
    if not win_shortcut.is_platform_supported():
        # 非 Windows 上复选框根本不存在，这条分支由下面的用例覆盖。
        calls = _drive_welcome_dialog(monkeypatch, check_shortcut=True)
        assert calls == []
        return
    calls = _drive_welcome_dialog(monkeypatch, check_shortcut=True)
    assert len(calls) == 1


def test_checkbox_presence_follows_the_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """控件的**存在性**随平台走（不是"出现但点了没用"）。"""
    # ★ 记"状态"而不是控件本身：QMessageBox 出作用域后会被 GC，事后再读它的子控件
    #   会拿到已删除的 C++ 对象（libshiboken RuntimeError）。
    seen: list[tuple[bool, bool]] = []

    def fake_exec(box: QMessageBox) -> int:
        checkbox = box.findChild(QCheckBox, SHORTCUT_CHECKBOX_NAME)
        seen.append((checkbox is not None, checkbox.isChecked() if checkbox is not None else False))
        for button in box.buttons():
            if box.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole:
                button.click()
                break
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(toast_module.ToastManager, "instance", staticmethod(lambda: _StubToast()))
    _make_checker().show_welcome_dialog()

    assert len(seen) == 1
    found, checked = seen[0]
    if win_shortcut.is_platform_supported():
        assert found, "Windows 上必须出现复选框"
        assert checked is False, "必须**默认不勾**"
    else:
        assert not found, "非 Windows 不得出现该复选框"


def test_shortcut_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """创建失败/异常都只提示，不冒泡 —— 可选功能不得拖垮主流程。"""
    toast = _StubToast()
    monkeypatch.setattr(toast_module.ToastManager, "instance", staticmethod(lambda: toast))
    monkeypatch.setattr(
        win_shortcut,
        "create_shortcut_for_app",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")),
    )
    _make_checker().create_desktop_shortcut()
    assert toast.messages, "失败必须给出可见提示"

    toast.messages.clear()
    monkeypatch.setattr(
        win_shortcut,
        "create_shortcut_for_app",
        lambda *_a, **_k: win_shortcut.ShortcutResult(ok=False, status="failed", detail="nope"),
    )
    _make_checker().create_desktop_shortcut()
    assert toast.messages, "未成功也必须给出可见提示"


def test_settings_menu_action_is_guarded_by_platform_support() -> None:
    """结构断言（用 `ast`，不是 grep 全文）——菜单项必须**真的在平台分支内**。

    否则非 Windows 上会多出一个"点了没用"的菜单项，而那正是方案禁止的形态。
    用 ast 而不是字符串包含：要证明的是**包含关系**（`addAction` 在 `if` 体内），
    而不是"文件里出现过这两个名字"。
    """

    def calls_platform_guard(node: ast.AST) -> bool:
        return any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "is_platform_supported"
            for inner in ast.walk(node)
        )

    tree = ast.parse(MENU_SOURCE.read_text(encoding="utf-8"))

    guarded_add_action: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not calls_platform_guard(node.test):
            continue
        guarded_add_action.extend(
            inner
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "addAction"
        )
    assert guarded_add_action, "设置菜单里没有受 is_platform_supported() 保护的 addAction"

    connected = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
        and any(
            isinstance(arg, ast.Attribute) and arg.attr == "create_desktop_shortcut"
            for arg in ast.walk(node)
        )
    ]
    assert connected, "快捷方式菜单项没有接到 create_desktop_shortcut"
