"""`gui/views/identity_welcome.py` 的守卫：首启身份引导的**触发条件**。

## 为什么这个模块值得测（此前 0% 覆盖）

`maybe_show_identity_welcome()` 决定「要不要在启动时弹身份引导窗」。
它有三个分支，其中两个是**事故源**：

1. **已有身份时不该弹** —— 否则每次启动都骚扰老用户；
2. **身份库不可用时要静默跳过** —— 否则一个可选依赖缺失就让整个 GUI 崩在启动阶段
   （本仓 2026-11-23 刚在便携包里踩过同类问题：导入期抛异常导致进程直接死）。

第 2 条尤其值得固定：`except Exception: return False` 看起来「吞异常」，
但它在这里是**刻意的** —— 引导窗是锦上添花，不该阻断启动。

## 边界

`dialog.exec()` 是模态阻塞调用，这里 monkeypatch 掉它，只断言「有没有被调用」。
不验外观。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="需要 PySide6（gui extra）")

from omnicrawler.gui.views import identity_welcome as iw  # noqa: E402


class _Recorder:
    """记录 `exec()` 是否被调用。"""

    def __init__(self) -> None:
        self.called = False

    def __call__(self, *_args, **_kwargs) -> int:
        self.called = True
        return 0


@pytest.fixture
def exec_recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(iw.IdentityWelcomeDialog, "exec", recorder)
    return recorder


def test_no_identities_triggers_welcome(qapp, exec_recorder, monkeypatch) -> None:
    """没有本地身份时必须弹引导。"""

    class _EmptyStore:
        def list_usernames(self) -> list[str]:
            return []

    monkeypatch.setattr("omnicrawler.plugins.identity.IdentityStore", _EmptyStore)
    assert iw.maybe_show_identity_welcome() is True
    assert exec_recorder.called, "无身份时没有弹出引导窗"


def test_existing_identity_does_not_trigger(qapp, exec_recorder, monkeypatch) -> None:
    """**核心守卫**：已有身份时不得弹窗 —— 否则每次启动都骚扰老用户。"""

    class _Store:
        def list_usernames(self) -> list[str]:
            return ["starlife"]

    monkeypatch.setattr("omnicrawler.plugins.identity.IdentityStore", _Store)
    assert iw.maybe_show_identity_welcome() is False
    assert not exec_recorder.called, "已有身份却仍弹了引导窗"


def test_identity_store_failure_is_silent(qapp, exec_recorder, monkeypatch) -> None:
    """**反向断言**：身份库不可用时必须静默跳过，不得让启动崩掉。

    引导窗是锦上添花；一个可选依赖缺失不该阻断整个 GUI。
    """

    class _BrokenStore:
        def __init__(self) -> None:
            raise RuntimeError("keyring 不可用")

    monkeypatch.setattr("omnicrawler.plugins.identity.IdentityStore", _BrokenStore)
    assert iw.maybe_show_identity_welcome() is False
    assert not exec_recorder.called, "身份库异常时不该弹窗"


def test_dialog_can_be_constructed_offscreen(qapp) -> None:
    """引导窗本身必须可在无显示环境构造（CI 与打包冒烟都靠这个）。"""
    dialog = iw.IdentityWelcomeDialog()
    try:
        assert dialog is not None
    finally:
        dialog.deleteLater()
