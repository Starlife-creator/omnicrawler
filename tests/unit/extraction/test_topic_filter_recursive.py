"""S2.5.31：主题匹配递归 + filter 深拷贝。"""

from __future__ import annotations

from omnicrawl.extraction.topic_filter import evaluate_topic, filter_records


def test_nested_list_field_matches() -> None:
    record = {"title": "公告", "tags": ["财报", "业绩"]}
    decision = evaluate_topic(record, {"include_any": ["财报"]})
    assert decision.matched is True
    assert "财报" in decision.included


def test_nested_dict_field_matches() -> None:
    record = {"title": "公告", "meta": {"category": "年度报告", "year": 2024}}
    decision = evaluate_topic(record, {"include_any": ["年度报告"]})
    assert decision.matched is True


def test_cross_field_no_false_hit() -> None:
    # "采集报告" 由 字段A"数据采集" + 字段B"报告" 拼接而成——不得跨字段命中
    record = {"title": "数据采集", "category": "报告"}
    decision = evaluate_topic(record, {"include_any": ["采集报告"]})
    assert decision.matched is False


def test_match_on_restricts_top_level_fields() -> None:
    record = {"title": "公告", "body": "利润增长"}
    decision = evaluate_topic(record, {"include_any": ["利润"], "match_on": ["title"]})
    assert decision.matched is False


def test_filter_records_does_not_mutate_input() -> None:
    record = {"title": "公告", "tags": ["财报"]}
    snapshot = dict(record)
    filtered = filter_records([record], {"enabled": True, "include_any": ["财报"]})
    assert len(filtered) == 1
    assert "_topic_match" in filtered[0]
    assert "_topic_match" not in record
    assert record == snapshot


def test_filter_records_disabled_returns_original_list() -> None:
    record = {"title": "公告"}
    records = [record]
    assert filter_records(records, {"enabled": False}) is records
