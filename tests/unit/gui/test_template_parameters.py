"""`gui/views/template_parameters.py` 的守卫：模板参数对话框的**校验契约**。

## 为什么这个模块值得测（此前 0% 覆盖）

它把「用户填的字符串」交给 `validate_parameters(..., strict=True)` 之后才
放行。两个关键契约：

1. **校验不过就不许关**（`_validate` 走 `QMessageBox.warning` 并 `return`，
   不调 `accept()`）—— 否则非法参数会被静默接受，错误在**跑任务时**才爆；
2. **`values()` 必须返回校验后的规范化值**，而不是控件里的原始字符串
   （`QComboBox` 存的是 `currentData()`，可能是 bool/int 而不是文本）。

## 边界

不验外观布局。`QMessageBox.warning` 是模态的，这里 monkeypatch 掉它，
只断言「是否被调用」与「对话框有没有 accept」—— 那才是行为差异。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="需要 PySide6（gui extra）")

from PySide6.QtWidgets import QLineEdit, QMessageBox  # noqa: E402

from omnicrawler.gui.views import template_parameters as tp  # noqa: E402


@pytest.fixture
def warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """拦截模态警告框，记录消息。"""
    seen: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda _parent, _title, text: seen.append(str(text))),
    )
    return seen


def _make(declarations, initial=None, *, qapp):
    return tp.TemplateParametersDialog(declarations, initial or {})


def test_values_normalizes_combo_to_underlying_data(qapp) -> None:
    """`QComboBox` 必须返回 `currentData()`（bool），不是显示文本。"""
    dialog = _make({"flag": {"type": "boolean", "enum": [True, False]}}, {"flag": False}, qapp=qapp)
    try:
        values = dialog.values()
        assert values.get("flag") is False, f"combo 取了显示文本而非数据：{values}"
    finally:
        dialog.deleteLater()


def test_values_reads_line_edit_text(qapp) -> None:
    """文本框的参数必须原样读出。"""
    dialog = _make({"name": {"type": "string", "default": "abc"}}, qapp=qapp)
    try:
        assert dialog.values().get("name") == "abc"
    finally:
        dialog.deleteLater()


def test_invalid_parameter_does_not_accept_the_dialog(qapp, warnings) -> None:
    """**核心守卫**：校验失败时不得 accept，且必须提示用户。

    如果这里 accept 了，非法参数会被静默接受 —— 任务跑到一半才失败。
    """
    dialog = _make({"count": {"type": "integer", "required": True}}, qapp=qapp)
    try:
        # 把必填项清空
        control = dialog.inputs["count"]
        assert isinstance(control, QLineEdit)
        control.setText("")
        dialog._validate()
        assert warnings, "校验失败却没有提示用户"
        assert dialog.result() != dialog.DialogCode.Accepted, (
            "校验失败仍然 accept 了 —— 非法参数会被静默接受"
        )
    finally:
        dialog.deleteLater()


def test_valid_parameter_accepts(qapp, warnings) -> None:
    """合法参数必须 accept，且不弹警告。"""
    dialog = _make({"count": {"type": "integer", "required": True, "default": 3}}, qapp=qapp)
    try:
        dialog._validate()
        assert not warnings, f"合法参数却报了警告：{warnings}"
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


def test_sensitive_parameter_uses_password_echo(qapp) -> None:
    """标了 `sensitive` 的参数必须遮蔽输入（不能明文显示密钥）。"""
    dialog = _make({"token": {"type": "string", "sensitive": True}}, qapp=qapp)
    try:
        control = dialog.inputs["token"]
        assert isinstance(control, QLineEdit)
        assert control.echoMode() == QLineEdit.EchoMode.Password, (
            "sensitive 参数没有遮蔽输入 —— 密钥会明文显示在界面上"
        )
    finally:
        dialog.deleteLater()


def test_required_parameter_is_marked_accessible(qapp) -> None:
    """必填项的无障碍名称必须带「（必填）」标记（屏幕阅读器靠它）。"""
    dialog = _make({"must": {"type": "string", "required": True}}, qapp=qapp)
    try:
        assert "必填" in dialog.inputs["must"].accessibleName()
    finally:
        dialog.deleteLater()
