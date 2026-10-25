"""B-P2-13：canonical 剥离 fragment；容量上限拒绝新增并告警；集合加锁；重复证据回归继续落库。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from omnicrawler.quality.dupe_filter import DualLayerDupeFilter, canonical_url
from omnicrawler.quality.observation_store import ObservationStore
from omnicrawler.quality.shadow_repair import ShadowComparison, candidate_rule


class TestCanonical:
    def test_fragment_stripped(self) -> None:
        assert canonical_url("https://example.com/a#section-top") == "https://example.com/a"

    def test_fragment_stripped_with_query(self) -> None:
        assert canonical_url("https://example.com/a?b=2#frag") == "https://example.com/a?b=2"


class TestCapacityGuard:
    def test_capacity_rejects_new_url_and_warns_once(self, caplog: pytest.LogCaptureFixture) -> None:
        dupe = DualLayerDupeFilter(max_urls=2)
        assert dupe.url_seen("https://example.com/1") is False
        assert dupe.url_seen("https://example.com/2") is False
        # 达上限后新条目不登记；返回 False（不阻断调用方），但发出一次性降级告警
        assert dupe.url_seen("https://example.com/3") is False
        assert dupe.size()["urls"] == 2
        assert any("容量" in record.message and "去重" in record.message for record in caplog.records)

    def test_concurrent_url_seen_keeps_consistent_size(self) -> None:
        dupe = DualLayerDupeFilter(max_urls=50)
        errors: list[Exception] = []

        def worker(worker_id: int) -> None:
            try:
                for index in range(50):
                    dupe.url_seen(f"https://example.com/{worker_id}/{index}")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert dupe.size()["urls"] == 50


class TestDuplicateEvidenceKeepsRegression:
    def test_repeated_evidence_hash_does_not_skip_regression_update(self, tmp_path: Path) -> None:
        samples = tuple(f"sample-{i}" for i in range(10))
        candidate = candidate_rule("title", "css", "h1.old", "h1.new", samples)
        db = tmp_path / "obs.db"
        with ObservationStore(db) as store:
            store.promote_for_candidate(candidate)
            good = ShadowComparison(
                old_records=100, new_records=105, old_quality=0.5, new_quality=0.8,
                false_matches=0, historical_compatible=True, evidence_sha256="same-digest",
            )
            store.record_observation(candidate, good)
            bad = ShadowComparison(
                old_records=105, new_records=100, old_quality=0.8, new_quality=0.5,
                false_matches=1, historical_compatible=True, evidence_sha256="same-digest",
            )
            # 重复证据哈希不再提前 return None：失败计数/风险更新继续落库
            store.record_observation(candidate, bad)
            row = store.snapshot(candidate.candidate_id)[0]
            assert row["shadow_fail_count"] == 1
            assert row["shadow_pass_count"] == 1
            assert row["false_positive_risk"] > 0
