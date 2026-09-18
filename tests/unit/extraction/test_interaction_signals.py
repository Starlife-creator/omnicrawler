"""走查 R3.2：交互信号必须**要么变成动作、要么变成明确告警**，不能沉默。

背景（0.13.0 实测）：交互类场景 11 例里 8 例连配置都产不出来、另 3 例只拿到 2–9 条，
而分析阶段**从不产出任何动作配置、也不给任何提示**。用户看到的是「未能产出可用配置」
或一个条数偏少的结果，无从知道差的是"交互"这一步。

本文件守住两条取舍：
1. **能安全自动化的只有滚动**（不改服务端状态、不需选择器、轮数有界）⇒ 直接进 `browser.actions`；
2. 点击 / 表单 / iframe **不自动生成动作** ⇒ 只给告警并指向 `record-actions`
   （既有代码注释记录过：把 next-link 写成"点击下一页"会让入口页第一页内容全丢）。
"""

from __future__ import annotations

import pytest

from omnicrawler.extraction.intelligent_scraper import (
    _explain_no_config,
    _interaction_advice,
    _interaction_failure_hint,
    _interaction_signals,
    analyze_to_config,
)

_LIST_PAGE = (
    "<html><body><ul>"
    + "".join(
        f'<li class="item"><span class="title">T{i}</span><span class="price">${i}.00</span></li>'
        for i in range(6)
    )
    + "</ul></body></html>"
)
_PLAIN_PAGE = "<html><body><h1>Hello</h1><p>just text</p></body></html>"


class TestDetection:
    def test_plain_page_has_no_signals(self) -> None:
        assert _interaction_signals(_PLAIN_PAGE) == ()

    @pytest.mark.parametrize(
        ("snippet", "expected"),
        [
            ("<div class='load-more'>more</div>", "infinite_scroll"),
            ("<script>new IntersectionObserver(fn)</script>", "infinite_scroll"),
            # ★ 实测形态（quotes.toscrape.com/scroll）：jQuery 滚动接线，没有
            #   IntersectionObserver 也没有 load-more 类名 —— 早期版本完全漏检
            ("<script>$(window).on('scroll', function(){ fetchNext(); });</script>", "infinite_scroll"),
            ("<script>window.addEventListener('scroll', fn)</script>", "infinite_scroll"),
            ("<iframe src='https://example.org/embed'></iframe>", "iframe"),
            ("<form action='/search'><input name='q'></form>", "form"),
            ("<div id='cookie-consent'>we use cookies</div>", "cookie_banner"),
            ("<div id='app'></div>", "spa_shell"),
        ],
    )
    def test_each_signal_is_detected(self, snippet: str, expected: str) -> None:
        assert expected in _interaction_signals(snippet)

    def test_detection_uses_structure_not_free_text(self) -> None:
        """自由文本里出现 "load more" 不该触发 —— 否则长正文会误报。"""
        assert _interaction_signals("<p>please load more data from the server</p>") == ()

    def test_bare_scroll_word_is_not_enough(self) -> None:
        """CSS 的 `overflow: scroll` 到处都是 —— 光有 "scroll" 不算证据。"""
        assert _interaction_signals("<div style='overflow: scroll'>x</div>") == ()

    def test_markup_token_alone_is_enough(self) -> None:
        assert "infinite_scroll" in _interaction_signals("<div id='infinite-scroll'></div>")


class TestAdvice:
    def test_scroll_signal_is_reported_as_auto_configured(self) -> None:
        advice = " ".join(_interaction_advice(("infinite_scroll",), scroll_rounds=12))
        assert "已自动配置" in advice
        assert "scroll_bottom×12" in advice

    def test_manual_signals_point_to_recorder_and_do_not_auto_act(self) -> None:
        for signal in ("cookie_banner", "iframe", "form", "spa_shell"):
            advice = " ".join(_interaction_advice((signal,), scroll_rounds=0))
            assert "record-actions" in advice, signal
            assert "不自动生成" in advice, signal

    def test_no_signals_no_advice(self) -> None:
        assert _interaction_advice((), scroll_rounds=0) == []


class TestConfigWiring:
    def test_scroll_signal_forces_browser_and_emits_actions(self) -> None:
        config = analyze_to_config(_LIST_PAGE, url="https://shop.example/list", scroll_rounds=12)
        assert config["source"]["kind"] == "browser"
        actions = config["browser"]["actions"]
        assert [item["action"] for item in actions] == ["wait_ms", "scroll_bottom"]
        assert actions[1]["times"] == 12

    def test_without_scroll_signal_no_actions_and_no_browser_block(self) -> None:
        config = analyze_to_config(_LIST_PAGE, url="https://shop.example/list")
        assert "actions" not in (config.get("browser") or {})
        assert not config.get("browser")

    def test_rendered_keeps_browser_but_only_adds_actions_when_asked(self) -> None:
        config = analyze_to_config(_LIST_PAGE, url="https://shop.example/list", rendered=True)
        assert config["source"]["kind"] == "browser"
        assert "actions" not in config["browser"]


class TestFailureHintNamesTheCause:
    def _explain(self, *signals: str) -> str:
        return " ".join(
            _explain_no_config("<html><body><p>x</p></body></html>", static_top=None,
                               rendered="<html><body><p>x</p></body></html>", signals=signals)
        )

    def test_iframe_is_named(self) -> None:
        assert "iframe" in self._explain("iframe")

    def test_cookie_banner_is_named(self) -> None:
        text = self._explain("cookie_banner")
        assert "cookie" in text.lower() or "consent" in text.lower()
        assert "record-actions" in text

    def test_form_is_named(self) -> None:
        assert "表单" in self._explain("form")

    def test_spa_shell_is_named(self) -> None:
        assert "SPA" in self._explain("spa_shell")

    def test_scroll_signal_advises_more_rounds(self) -> None:
        assert "scroll_bottom" in self._explain("infinite_scroll")

    def test_no_signal_falls_back_to_generic_advice(self) -> None:
        assert "record-actions" in self._explain()

    def test_hints_are_mutually_distinguishable(self) -> None:
        seen = {self._explain(sig) for sig in ("iframe", "cookie_banner", "form", "spa_shell")}
        assert len(seen) == 4, "四种交互信号必须给出四种不同的下一步"

    def test_specific_hint_wins_over_generic_wording(self) -> None:
        """有具体信号时不该退回泛泛的"很可能要交互"。"""
        assert "很可能要" not in self._explain("iframe")
        assert _interaction_failure_hint(()) == []
