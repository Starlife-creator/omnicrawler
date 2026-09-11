"""设计系统契约测试：把「不报错的缺陷」变成会失败的断言。

对应 `audit-20260805` 的 §A-18 / §A-19 / §A-20 / §A-23 / §A-31 / §A-38 / §A-39。

这些缺陷的共同点是**不报错、静默无效**：

* QSS 里写了 Qt 不实现的属性/伪状态（`:focus-visible`、`outline`）→ 焦点样式整块是死规则；
* 「界面缩放」只改了 app 字体，被 QSS 的绝对 px 覆盖 → 设置改了但界面毫无变化；
* 色盲主题的色值绕过了色值白名单 → 开发期严格检查下直接崩；
* `rgba` 令牌解析失败静默回退成一个猜测的颜色。

它们都没有让任何既有测试失败，所以必须由断言来守——本文件就是那只手。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="设计系统测试需要 PySide6")
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.design_system import (
    SCALE_MAX,
    SCALE_MIN,
    ThemeManager,
    _SignalProxy,
    assert_qss_supported,
    rgba_token_to_qcolor,
    scaled_font_px,
    stylesheet,
    theme_tokens,
)

_GUI_ROOT = Path(__file__).resolve().parents[2] / "src" / "omnicrawler" / "gui"
_THEMES = ("light", "dark", "high_contrast")


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    return app


def _declarations(qss: str, selector: str) -> dict[str, str]:
    """取某选择器的声明（同名选择器出现多次时后者覆盖前者，与 Qt 的层叠一致）。

    必须先剥掉注释：注释自身没有花括号，会被 `[^{}]+` 并进**后面第一个**选择器，
    于是那一条选择器永远匹配不上（实测踩到过）。
    """
    found: dict[str, str] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", " ", qss, flags=re.S)):
        parts = [part.strip() for part in selectors.split(",")]
        if selector not in parts:
            continue
        for declaration in body.split(";"):
            if ":" in declaration:
                name, _, value = declaration.partition(":")
                found[name.strip()] = value.strip()
    return found


def _px(value: str) -> int:
    match = re.match(r"(-?\d+)px", value.strip())
    assert match, f"不是 px 值：{value!r}"
    return int(match.group(1))


def _box_total(border: str, padding: str) -> int:
    """控件单边的「边框 + 内边距」总量 —— 它不变，焦点态就不会让内容位移。"""
    return _px(border) + _px(padding.split()[0])


# ---------------------------------------------------------------------------
# §A-18 / §A-38：QSS 只能用 Qt 实现的东西
# ---------------------------------------------------------------------------


def test_generated_qss_has_no_unsupported_property_or_pseudo() -> None:
    for theme in _THEMES:
        qss = stylesheet(theme_tokens(theme))
        assert_qss_supported(qss, context=theme)  # 不抛即通过


def test_guard_detects_planted_dead_rules() -> None:
    """★ 守卫必须会咬：否则它只是装饰（本项目的既有教训）。"""
    with pytest.raises(ValueError, match="outline"):
        assert_qss_supported("QPushButton { outline: 2px solid #000; }")
    with pytest.raises(ValueError, match="focus-visible"):
        assert_qss_supported("QPushButton:focus-visible { color: #000; }")
    with pytest.raises(ValueError, match="letter-spacing"):
        assert_qss_supported("QLabel { letter-spacing: 1px; }")


def test_guard_ignores_comments() -> None:
    """注释里提到这些名字（如说明「不要写 outline」）不应误报。"""
    assert_qss_supported("/* 不要写 outline，Qt 不支持 */\nQLabel { color: #000; }")


# ---------------------------------------------------------------------------
# §A-20：全部主题变体都要能过色值白名单
# ---------------------------------------------------------------------------


def test_all_theme_variants_pass_strict_hex_guard(qt_app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 曾经的真实崩溃：开启色盲友好 + 开发期严格色值检查 → ValueError。

    色盲调色板是在 `theme_tokens()` 里另行构造的，而白名单只遍历三个主题常量，
    于是 6 个组合里有 3 个直接抛错（实测 37/33/37 处「非法裸 hex」）。
    """
    monkeypatch.setenv("OMNICRAWL_GUI_STRICT_HEX", "1")
    for theme in _THEMES:
        for color_blind in (False, True):
            ThemeManager.instance().apply(
                qt_app, theme=theme, color_blind_friendly=color_blind, scale=125
            )


def test_colorblind_palette_values_are_whitelisted() -> None:
    tokens = theme_tokens("light", color_blind_friendly=True)
    qss = stylesheet(tokens)
    assert tokens.primary.upper() in qss.upper()


# ---------------------------------------------------------------------------
# §A-23：缩放必须到达 QSS（否则「界面缩放」形同虚设）
# ---------------------------------------------------------------------------


def test_scale_reaches_both_qss_and_app_font(qt_app: QApplication) -> None:
    """★ 此前 QSS 恒为 14px（缩放只改了 app 字体，被 QSS 覆盖）。"""
    manager = ThemeManager.instance()

    manager.apply(qt_app, theme="light", scale=100)
    base_qss = qt_app.styleSheet()
    base_font = qt_app.font().pixelSize()
    assert f"font-size: {scaled_font_px('body', scale=100)}px" in base_qss

    manager.apply(qt_app, theme="light", scale=150)
    big_qss = qt_app.styleSheet()
    big_font = qt_app.font().pixelSize()

    assert scaled_font_px("body", scale=100) == 14
    assert scaled_font_px("body", scale=150) == 21
    assert big_qss != base_qss, "QSS 未随缩放变化——缩放对 QSS 控件无效"
    assert "font-size: 21px" in big_qss
    assert base_font == 14 and big_font == 21


def test_qss_cache_invalidates_on_scale_change_only(qt_app: QApplication) -> None:
    """只改缩放也要重建 QSS（缓存键必须含 scale）。"""
    manager = ThemeManager.instance()
    manager.apply(qt_app, theme="light", scale=100)
    first = qt_app.styleSheet()
    manager.apply(qt_app, theme="light", scale=140)  # 令牌不变，只有缩放变
    assert qt_app.styleSheet() != first


def test_scale_is_clamped_to_declared_range() -> None:
    assert scaled_font_px("body", scale=10) == scaled_font_px("body", scale=SCALE_MIN)
    assert scaled_font_px("body", scale=999) == scaled_font_px("body", scale=SCALE_MAX)


def test_only_design_system_reads_font_size_directly() -> None:
    """★ 防回退：字号必须经 `scaled_font_px` 取，否则该处不会跟随缩放。

    这条守的是**整类**缺陷，而不是某一个文件——新写的内联样式若直接取
    `FONT_SIZE[...]`，会在这里失败。
    """
    offenders: list[str] = []
    for path in _GUI_ROOT.rglob("*.py"):
        if path.name == "design_system.py" or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"FONT_SIZE\[", text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(_GUI_ROOT)}:{line}")
    assert not offenders, (
        "以下位置直接取 FONT_SIZE（不跟随界面缩放），请改用 scaled_font_px()："
        + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# §A-31：焦点态不得改变控件外框尺寸
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "focused"),
    [("QPushButton", "QPushButton:focus")],
)
def test_focus_keeps_widget_box_size(base: str, focused: str) -> None:
    qss = stylesheet(theme_tokens("light"))
    base_decls = _declarations(qss, base)
    focus_decls = _declarations(qss, focused)
    assert "padding" in focus_decls, "加粗焦点边框必须补偿内边距，否则内容会跳 1px"
    assert _box_total(base_decls["border"], base_decls["padding"]) == _box_total(
        focus_decls["border"], focus_decls["padding"]
    )


def test_input_focus_keeps_widget_box_size() -> None:
    qss = stylesheet(theme_tokens("light"))
    base_decls = _declarations(qss, "QLineEdit")
    focus_decls = _declarations(qss, "QLineEdit:focus")
    assert _box_total(base_decls["border"], base_decls["padding"]) == _box_total(
        focus_decls["border"], focus_decls["padding"]
    )


# ---------------------------------------------------------------------------
# §A-39：rgba 令牌解析不得静默回退
# ---------------------------------------------------------------------------


def test_rgba_token_parses_valid_values() -> None:
    color = rgba_token_to_qcolor("rgba(1, 2, 3, 0.5)")
    assert (color.red(), color.green(), color.blue()) == (1, 2, 3)
    assert color.alpha() == 128  # 0.5 * 255 四舍五入


def test_rgba_token_raises_instead_of_silently_falling_back() -> None:
    for bad in ("#fff", "rgb(1,2,3)", "rgba(1,2,3)", ""):
        with pytest.raises(ValueError, match="rgba"):
            rgba_token_to_qcolor(bad)


# ---------------------------------------------------------------------------
# §A-19：信号代理不得静默降级 / 不得因异常永久静音
# ---------------------------------------------------------------------------


def _warning_records(caplog: pytest.LogCaptureFixture) -> Iterator[logging.LogRecord]:
    return (record for record in caplog.records if record.levelno >= logging.WARNING)


def test_signal_proxy_trims_arguments_for_zero_arg_slots() -> None:
    """Qt 语义：槽可以少收实参。这条行为必须保留。"""
    proxy = _SignalProxy()
    seen: list[int] = []
    proxy.connect(lambda: seen.append(1))
    proxy.emit("ignored", 2)
    assert seen == [1]


def test_signal_proxy_surfaces_type_error_from_inside_callback(caplog: pytest.LogCaptureFixture) -> None:
    """★ 回调**自己**抛 TypeError 是真 bug，不能被「无参重试」降级掩盖。"""
    proxy = _SignalProxy()
    calls: list[object] = []

    def broken_callback(payload: object) -> None:
        calls.append(payload)
        raise TypeError("回调内部真的错了")

    proxy.connect(broken_callback)
    with caplog.at_level(logging.WARNING, logger="omnicrawler.gui.design_system"):
        proxy.emit("x")

    assert len(calls) == 1, "不应把「回调内部 TypeError」当成实参裁剪去重试"
    assert any("回调" in record.getMessage() for record in _warning_records(caplog))


class _CallbackBlowUp(BaseException):
    """自定义 BaseException。

    刻意**不用** `KeyboardInterrupt`：pytest 把它视为「测试运行被中断」并直接
    中止整个会话（实测会让整轮测试在第一个用例处停下），而不是当作普通异常断言。
    `except Exception` 同样不会捕获本类，因此它足以验证「BaseException 漏出时
    `_emitting` 是否复位」。
    """


def test_signal_proxy_recovers_after_base_exception() -> None:
    """★ 回调抛 BaseException 时 `_emitting` 必须复位，否则信号总线永久静默。"""
    proxy = _SignalProxy()

    def boom() -> None:
        raise _CallbackBlowUp

    proxy.connect(boom)
    with pytest.raises(_CallbackBlowUp):
        proxy.emit()

    # 只有 RuntimeError（C++ 对象已销毁）才会自动摘除订阅者，所以这里显式断开，
    # 否则第二次 emit 会再次撞上同一个回调，测的就不是「是否已被永久静音」了。
    proxy.disconnect(boom)
    seen: list[str] = []
    proxy.connect(lambda: seen.append("ok"))
    proxy.emit()
    assert seen == ["ok"], "信号总线在异常后被永久静音（_emitting 未复位）"
