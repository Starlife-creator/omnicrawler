"""B-P3-4：Markdown 降维补锚点 href 与表格竖线分隔；主题词英文词边界匹配；
仅保留记录才深拷贝。
"""

from __future__ import annotations

from omnicrawler.extraction.markdown_reducer import html_to_markdown
from omnicrawler.extraction.topic_filter import evaluate_topic, filter_records

# ── 锚点 href 不丢失 ─────────────────────────────────────────────────────

def test_anchor_href_emitted_in_markdown() -> None:
    html = "<p>去 <a href='https://example.com'>官网</a> 看看</p>"
    markdown = html_to_markdown(html)
    assert "[官网](https://example.com)" in markdown


# ── 表格单元格补 `|` 列分隔与行尾竖线 ─────────────────────────────────────

def test_table_cells_are_pipe_separated() -> None:
    html = (
        "<table><tr><th>名称</th><th>价格</th></tr>"
        "<tr><td>书A</td><td>12</td></tr></table>"
    )
    markdown = html_to_markdown(html)
    assert "| 名称 | 价格 |" in markdown
    assert "| 书A | 12 |" in markdown


# ── 主题词：英文短词按词边界，不命中单词内部 ────────────────────────────────

def test_english_term_does_not_match_inside_word() -> None:
    decision = evaluate_topic(
        {"title": "category report"}, {"include_any": ["cat"]},
    )
    assert decision.matched is False


def test_english_term_matches_boundary() -> None:
    decision = evaluate_topic(
        {"title": "the cat sat on the mat"}, {"include_any": ["cat"]},
    )
    assert decision.matched is True


def test_chinese_term_still_matches_substring() -> None:
    decision = evaluate_topic(
        {"title": "年度财报摘要"}, {"include_any": ["财报"]},
    )
    assert decision.matched is True


# ── 仅保留记录才深拷贝：未入选记录不被写入元数据 ────────────────────────────

def test_dropped_record_not_copied_and_original_unmutated() -> None:
    record = {"title": "无关内容", "tags": ["其他"]}
    snapshot = dict(record)
    filtered = filter_records(
        [record], {"enabled": True, "include_any": ["财报"]},
    )
    assert filtered == []
    assert "_topic_match" not in record
    assert record == snapshot
