"""B-P3-11：观察候选表 snapshot 补 LIMIT 与分页，大集不再整表拉取。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.quality.observation_store import ObservationStore
from omnicrawler.quality.shadow_repair import candidate_rule


def _seed(store: ObservationStore, count: int) -> None:
    samples = tuple(f"sample-{i}" for i in range(10))
    for index in range(count):
        candidate = candidate_rule(
            f"field{index}", "css", f"old-{index}", f"new-{index}", samples
        )
        store.promote_for_candidate(candidate)


def test_snapshot_limit_and_offset_paginate(tmp_path: Path) -> None:
    db = tmp_path / "obs.db"
    with ObservationStore(db) as store:
        _seed(store, 12)
        first = store.snapshot(limit=5)
        second = store.snapshot(limit=5, offset=5)
        third = store.snapshot(limit=5, offset=10)
        assert len(first) == 5
        assert len(second) == 5
        assert len(third) == 2
        # 两页不重叠
        first_ids = {row["candidate_id"] for row in first}
        second_ids = {row["candidate_id"] for row in second}
        assert not (first_ids & second_ids)


def test_snapshot_limit_clamped_to_bounds(tmp_path: Path) -> None:
    db = tmp_path / "obs.db"
    with ObservationStore(db) as store:
        _seed(store, 3)
        # 超大 limit 被钳制到上限，负 offset 归零，不抛错
        rows = store.snapshot(limit=100_000, offset=-5)
        assert len(rows) == 3
