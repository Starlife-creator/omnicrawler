"""运行间「删除」语义的端到端验收——INV-008 修正后声明所引证据。

**为什么单开一个文件**：`docs/SECURITY_AND_COMPLIANCE.md` 的 INV-008 原先写作
「删除需连续确认」，实现列指向 `updates.confirm_missing_runs`、证据列指向
`test_v110_features.py` 的连续缺失测试。实测两者都不成立：

- `updates.confirm_missing_runs` 在 `src` 中**只有默认值与一条校验**，没有任何消费点；
- `test_v110_features.py` **全仓不存在**（INV-004 也引用同一不存在的文件）。

按用户裁决（2026-09-13，方案 B：修正声明）把该不变量改成**代码实际实现的行为**，
本文件即修正后声明所引用的证据。修正后的语义是：

1. 记录消失只在**两次运行对比**（`review.run_compare.compare_runs`）时判定；
   主路径 `state.track_semantic_changes` 只遍历本次记录，永远产不出 `removed`。
2. 且只有在**后一次运行正常完成**时才确认为 `removed`；后一次未完成
   （running / cancelled / failed）一律降级为 `possibly_removed` 且 `confirmed=False`
   —— 这是"不轻判删除"的保护，也是修正后 INV-008 的真实含义。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.review.run_compare import compare_runs
from omnicrawler.state import StateStore

_URL = "https://example.org/list"
_REQUEST = CrawlRequest(_URL)


def _record(rid: int, title: str) -> ExtractedRecord:
    return ExtractedRecord(_URL, "item", {"id": rid, "title": title})


def _run(state: StateStore, records: list[ExtractedRecord]) -> str:
    """开一个 run 并落记录；是否调 `finish_run` 由调用方决定（用于构造"未完成"）。"""
    run_id = state.start_run("compare", "config.yaml")
    state.save_records(run_id, _REQUEST, records)
    return run_id


def test_disappeared_record_is_confirmed_removed_between_complete_runs(tmp_path: Path) -> None:
    """后一次运行正常完成 ⇒ 消失的记录被确认为 removed。"""
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = _run(state, [_record(1, "A"), _record(2, "B")])
        state.finish_run(first, "succeeded", {})
        second = _run(state, [_record(1, "A")])
        state.finish_run(second, "succeeded", {})

        report = compare_runs(state, first, second)

    assert report["after_run_status"] == "succeeded"
    assert report["removed"] == 1, f"完整运行间应确认删除：{report}"
    assert report["possibly_removed"] == 0
    removed = [item for item in report["changes"] if item["change_type"] == "removed"]
    assert removed, f"应有一条 removed：{report['changes']}"
    assert removed[0]["identity"] == "item|id:2", f"被删的应是 id=2：{removed[0]}"
    assert removed[0]["confirmed"] is True


def test_unfinished_after_run_downgrades_deletion_to_possibly_removed(tmp_path: Path) -> None:
    """后一次运行**未完成** ⇒ 不得确认删除，降级为 possibly_removed。"""
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = _run(state, [_record(1, "A"), _record(2, "B")])
        state.finish_run(first, "succeeded", {})
        # 不调 finish_run：该 run 仍是 running（等价于取消/中断后未收尾）
        interrupted = _run(state, [_record(1, "A")])

        report = compare_runs(state, first, interrupted)

    assert report["after_run_status"] == "running"
    assert report["removed"] == 0, f"后一次未完成时不得确认删除：{report}"
    assert report["possibly_removed"] == 1, f"应降级为 possibly_removed：{report}"
    items = [item for item in report["changes"] if item["change_type"] == "possibly_removed"]
    assert items, f"应有 possibly_removed 条目：{report['changes']}"
    assert items[0]["confirmed"] is False


def test_same_title_different_identity_is_not_merged(tmp_path: Path) -> None:
    """同名但业务上不同的记录必须保留为两条（身份按 id，不按标题）。"""
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = _run(state, [_record(1, "同名")])
        state.finish_run(first, "succeeded", {})
        second = _run(state, [_record(2, "同名")])
        state.finish_run(second, "succeeded", {})

        report = compare_runs(state, first, second)

    assert report["added"] == 1, f"id=2 应视为新增：{report}"
    assert report["removed"] == 1, f"id=1 应视为移除：{report}"
    assert report["modified"] == 0, f"同名不同身份不应被当成同一条的修改：{report}"
