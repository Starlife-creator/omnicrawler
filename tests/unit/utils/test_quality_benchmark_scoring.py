"""质量基准打分：每一类缺陷都必须让分数掉下来。

`score_records` 是**纯函数**（记录 + 真值 → 分数），因此这里不启服务、不跑流水线，
直接用合成记录把四类问题逐一测出来：

1. 记录没采全 → 完整性下降；
2. 字段值与真值不符 → 准确性下降；
3. 记录缺 `source_url` → 来源证据比例下降；
4. 流水线自报的 `evidence._quality.completeness` 被如实汇总（与外部比对互证）。

另有 :mod:`tests.integration.test_quality_benchmark` 跑真实任务做端到端 ratchet。
"""

from __future__ import annotations

from omnicrawler.services.quality_benchmark import (
    TASKS,
    BenchmarkTask,
    score_records,
)

_TASK = BenchmarkTask(
    name="unit",
    pages=(),
    item_selector="div.item",
    fields=(("title", "h1"), ("price", "span")),
    expected=(
        {"title": "A", "price": "1"},
        {"title": "B", "price": "2"},
    ),
)


def _record(title: str, price: str, *, url: str = "https://example.org/x", completeness: float = 1.0):
    return {
        "data": {"title": title, "price": price},
        "source_url": url,
        "evidence": {"_quality": {"completeness": completeness}},
    }


def test_perfect_records_score_full_marks() -> None:
    score = score_records(_TASK, [_record("A", "1"), _record("B", "2")])
    assert score.completeness == 1.0
    assert score.accuracy == 1.0
    assert score.evidence_ratio == 1.0
    assert score.ok is True


def test_missing_record_lowers_completeness() -> None:
    score = score_records(_TASK, [_record("A", "1")])
    assert score.completeness == 0.5
    assert score.matched_records == 1
    assert score.ok is False


def test_wrong_field_value_lowers_accuracy_only() -> None:
    score = score_records(_TASK, [_record("A", "999"), _record("B", "2")])
    assert score.completeness == 1.0, "记录条数是对的，完整性不应下降"
    assert score.accuracy == 0.75, "4 个期望字段里错了 1 个"
    assert score.ok is False


def test_missing_source_url_lowers_evidence_ratio() -> None:
    score = score_records(_TASK, [_record("A", "1", url=""), _record("B", "2")])
    assert score.evidence_ratio == 0.5
    assert score.ok is False


def test_pipeline_reported_completeness_is_aggregated() -> None:
    """流水线自报的字段完整度要如实汇总——它是与外部比对互证的另一侧。"""
    score = score_records(
        _TASK,
        [_record("A", "1", completeness=0.5), _record("B", "2", completeness=1.0)],
    )
    assert score.mean_field_completeness == 0.75


def test_records_without_quality_evidence_do_not_crash() -> None:
    score = score_records(_TASK, [{"data": {"title": "A", "price": "1"}}])
    assert score.mean_field_completeness == 0.0
    assert score.completeness == 0.5


def test_extra_records_do_not_inflate_scores() -> None:
    """多采到的记录不算「完整」——完整性以真值为准，避免以数量充数。"""
    score = score_records(_TASK, [_record("A", "1"), _record("Z", "9")])
    assert score.found_records == 2
    assert score.completeness == 0.5


def test_empty_records_score_zero() -> None:
    score = score_records(_TASK, [])
    assert score.completeness == 0.0
    assert score.accuracy == 0.0
    assert score.evidence_ratio == 0.0
    assert score.ok is False


def test_builtin_tasks_are_well_formed() -> None:
    """内置任务必须自带真值与抽取规则——否则基准没有可比对象。"""
    for task in TASKS:
        assert task.pages, f"{task.name} 没有页面"
        assert task.fields, f"{task.name} 没有字段"
        assert task.expected, f"{task.name} 没有期望结果"
        seeds = {path for path, _ in task.pages}
        assert "/" in seeds, f"{task.name} 缺少种子页"
        for entry in task.expected:
            assert set(entry) == {name for name, _ in task.fields}, f"{task.name} 真值字段与抽取字段不一致"
