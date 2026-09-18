"""走查 R2.3：自动分析失败必须**分原因**，而不是一句通用话。

背景（0.13.0 实测，2026-09-18 走查 R2.3）：10 例自动分析失败共用同一句
「自动分析未能产出可用配置（静态与浏览器渲染均无可用列表）」，
而它们实际落在四种完全不同的情形上 —— 响应为空、响应不是 HTML、
页面有结构但配置过不了自校验、必须交互才出现列表。
四种情形的下一步动作各不相同，用户拿不到区分依据只能挨个试。

本文件守住「按已掌握的事实分类，并给出该分支的下一步」。
"""

from __future__ import annotations

from dataclasses import dataclass

from omnicrawler.extraction.intelligent_scraper import _explain_no_config

_HTML_WITH_LIST = "<html><body><ul><li>x</li></ul></body></html>"
_HTML_WITHOUT_LIST = "<html><body><p>x</p></body></html>"


@dataclass
class _Pattern:
    count: int
    css_path: str


def _joined(lines: list[str]) -> str:
    return " ".join(lines)


def test_empty_body_points_to_policy_and_doctor() -> None:
    text = _joined(_explain_no_config("   ", static_top=None, rendered=""))
    assert "响应体为空" in text
    assert "doctor" in text and "security-report" in text


def test_non_html_body_points_to_api_discovery() -> None:
    text = _joined(_explain_no_config('{"a": 1}', static_top=None, rendered=""))
    assert "不是 HTML" in text
    assert "api-discover" in text


def test_existing_list_structure_points_to_field_tools() -> None:
    """★ 这一支最容易和"页面没有列表"混为一谈：结构在，是配置过不了自校验。"""
    text = _joined(
        _explain_no_config(
            _HTML_WITH_LIST, static_top=_Pattern(count=20, css_path="body > ul"), rendered=_HTML_WITH_LIST
        )
    )
    assert "有" in text and "候选列表结构" in text
    assert "field-suggest" in text
    assert "record-actions" not in text, "有结构时不该把用户引向「录制交互」"


def test_rendered_without_list_points_to_interaction() -> None:
    text = _joined(
        _explain_no_config(_HTML_WITHOUT_LIST, static_top=None, rendered=_HTML_WITHOUT_LIST)
    )
    assert "都没有发现重复列表结构" in text
    assert "record-actions" in text, "渲染过了还没有 ⇒ 多半要交互"


def test_render_unavailable_is_distinguished_from_render_empty() -> None:
    """渲染完全没取到内容，与"渲染了但没有列表"是不同的诊断。"""
    rendered_empty = _joined(
        _explain_no_config(_HTML_WITHOUT_LIST, static_top=None, rendered="")
    )
    rendered_ok = _joined(
        _explain_no_config(_HTML_WITHOUT_LIST, static_top=None, rendered=_HTML_WITHOUT_LIST)
    )
    assert rendered_empty != rendered_ok
    assert "且渲染未取到内容" in rendered_empty


def test_branches_are_mutually_distinguishable() -> None:
    """四种情形必须产出**四种不同**的说明 —— 否则等于又回到"一句话"。"""
    outputs = {
        _joined(_explain_no_config("", static_top=None, rendered="")),
        _joined(_explain_no_config('{"a":1}', static_top=None, rendered="")),
        _joined(
            _explain_no_config(
                _HTML_WITH_LIST,
                static_top=_Pattern(count=20, css_path="body > ul"),
                rendered=_HTML_WITH_LIST,
            )
        ),
        _joined(_explain_no_config(_HTML_WITHOUT_LIST, static_top=None, rendered=_HTML_WITHOUT_LIST)),
    }
    assert len(outputs) == 4, "四种失败情形必须给出四种不同诊断"


def test_every_branch_gives_a_next_step() -> None:
    """每条诊断都必须带可执行的下一步（否则只是换了个说法）。"""
    for lines in (
        _explain_no_config("", static_top=None, rendered=""),
        _explain_no_config('{"a":1}', static_top=None, rendered=""),
        _explain_no_config(
            _HTML_WITH_LIST, static_top=_Pattern(count=3, css_path="body > ul"), rendered=""
        ),
        _explain_no_config(_HTML_WITHOUT_LIST, static_top=None, rendered=_HTML_WITHOUT_LIST),
        _explain_no_config(_HTML_WITHOUT_LIST, static_top=None, rendered=""),
    ):
        assert any("下一步" in line for line in lines), lines
