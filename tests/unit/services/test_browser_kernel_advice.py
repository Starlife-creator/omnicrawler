"""「换内核再试」建议的守卫。

## 这批守卫防什么

盲目建议换内核是**有害的** —— 用户会以为是浏览器问题，而实际可能是
网络、选择器、或站点本身改了。所以判据必须区分「症状是否指向渲染层」。

最需要守住的一条：**反爬拦截不建议换内核**。
项目已明确拒绝 `stealth-browser-mcp` 那条路（规避风控），
换内核不能成为变相反检测的入口。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.browser_kernel_advice import (
    SwitchKernelAdvice,
    advise_switch,
    classify_failure,
)


@pytest.mark.parametrize(
    ("error_text", "symptom"),
    [
        ("Page load navigation timeout after 30000ms", "navigation_timeout"),
        ("script evaluate timeout in page", "script_timeout"),
        ("empty content but status 200", "empty_render"),
        ("403 forbidden", "anti_bot"),
        ("captcha required", "anti_bot"),
        ("connection refused: socket error", "network_error"),
        ("selector .item not found", "selector_miss"),
        ("parse json failed", "parse_error"),
        ("完全无法识别的错误文本", "unknown"),
    ],
)
def test_classify_covers_expected_symptoms(error_text: str, symptom: str) -> None:
    assert classify_failure(error_text) == symptom


@pytest.mark.parametrize(
    "error_text",
    [
        "Page load navigation timeout",
        "script evaluate timeout",
        "empty content status 200",
    ],
)
def test_render_layer_symptoms_suggest_switch(error_text: str) -> None:
    """**渲染层症状**才建议换内核。"""
    advice = advise_switch(error_text, current_engine="chromium")
    assert advice.suggest is True
    assert advice.target_engine and advice.target_engine != "chromium"


def test_anti_bot_explicitly_does_not_suggest_switch() -> None:
    """**反爬拦截不建议换内核。**

    这是最有意的设计：换内核不解决风控，而本项目已明确不做反检测。
    如果这条判据松了，功能会自己滑向被拒绝的方向。
    """
    advice = advise_switch(
        "site returned 403 forbidden with captcha", current_engine="chromium"
    )
    assert advice.suggest is False
    assert "反检测" in advice.reason or "风控" in advice.reason


def test_network_error_does_not_suggest_switch() -> None:
    """网络错误与内核无关，建议换内核只会误导。"""
    advice = advise_switch("DNS resolution failed", current_engine="chromium")
    assert advice.suggest is False


def test_selector_miss_does_not_suggest_switch() -> None:
    """选择器未命中是页面结构问题。"""
    advice = advise_switch("selector not found", current_engine="chromium")
    assert advice.suggest is False


def test_no_alternative_engine_does_not_suggest() -> None:
    """**本机没有别的内核时不建议** —— 建议一个装不上的东西让人困惑。"""
    advice = advise_switch(
        "navigation timeout",
        current_engine="chromium",
        available_engines=("chromium",),
    )
    assert advice.suggest is False
    assert "没有其他可用内核" in advice.reason


def test_message_is_empty_when_not_suggesting() -> None:
    """不建议时不该产出可显示的建议文本（否则界面会显示空按钮）。"""
    advice = advise_switch("403 forbidden", current_engine="chromium")
    assert advice.message() == ""


def test_message_mentions_target_engine() -> None:
    """建议时文本必须说出**换成哪个**，不能只说「换个内核」。"""
    advice = advise_switch(
        "navigation timeout",
        current_engine="chromium",
        available_engines=("chromium", "firefox"),
    )
    assert advice.suggest is True
    assert "firefox" in advice.message()


def test_advice_is_frozen() -> None:
    """建议对象必须不可变 —— 它会被多处读取，可变状态容易串。"""
    advice = advise_switch("navigation timeout", current_engine="chromium")
    with pytest.raises(AttributeError):
        advice.suggest = False  # type: ignore[misc]


def test_symptom_text_is_human_readable() -> None:
    """症状必须翻成人话，不能直接把 `navigation_timeout` 摆给用户。"""
    advice = advise_switch("navigation timeout", current_engine="chromium")
    assert "_" not in advice.symptom_text
    assert advice.symptom_text


def test_return_type_is_stable() -> None:
    advice = advise_switch("unknown blob")
    assert isinstance(advice, SwitchKernelAdvice)

# ── GUI 接线（逻辑层已测，这里验证真的接上了）────────────────────────
def test_task_canvas_wires_kernel_advice() -> None:
    """任务画布必须真的调用 `advise_switch`，而不是只在 UI 上放个按钮。

    单纯加按钮没有意义 —— 关键是失败诊断时**按症状决定要不要显示**。
    """
    from pathlib import Path

    canvas = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "omnicrawler"
        / "gui"
        / "views"
        / "task_canvas.py"
    )
    source = canvas.read_text(encoding="utf-8")
    assert "advise_switch" in source, "任务画布没有调用换内核建议"
    assert "_switch_kernel_btn" in source, "没有换内核按钮"
    assert "setVisible(advice.suggest)" in source, (
        "按钮可见性没有跟建议走 —— 会变成「永远显示」"
    )


def test_switch_button_hidden_when_not_suggested() -> None:
    """反爬/网络/选择器问题**不得**让换内核按钮出现。

    这条直接读源码断言接线方式：`setVisible(advice.suggest)`。
    如果写成 `setVisible(True)`，按钮会在任何失败时都出现，
    把用户引向「换内核能解决一切」的错误认知。
    """
    from pathlib import Path

    canvas = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "omnicrawler"
        / "gui"
        / "views"
        / "task_canvas.py"
    )
    source = canvas.read_text(encoding="utf-8")
    assert "_switch_kernel_btn.setVisible(True)" not in source, (
        "换内核按钮被无条件显示"
    )
