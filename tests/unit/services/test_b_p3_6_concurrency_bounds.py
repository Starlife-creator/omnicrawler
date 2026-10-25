"""B-P3-6：并发安全与内存边界回归测试。

覆盖：
- ProgressTracker 多线程推进/设百分无损坏，EMA 样本有界，事件快照可读；
- ApplicationService 配置缓存命中同一对象，reload 重置，并发读取不报错；
- 回归 fixture gzip 解压大小上限；
- 研究包 zip 流式打包后条目内容完整、清单存在。
"""

from __future__ import annotations

import copy
import random
import threading
import zipfile
from pathlib import Path

import pytest

from omnicrawler.core.config import DEFAULTS, AppConfig
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.services.application_service import ApplicationService
from omnicrawler.services.progress import ProgressTracker, StageSpec
from omnicrawler.services.research_package import create_research_package
import omnicrawler.services.regression_library as regression_library
from omnicrawler.services.regression_library import RegressionLibrary


# ── ProgressTracker 并发 ──────────────────────────────────────────────────────


def test_progress_tracker_thread_safe_and_samples_bounded() -> None:
    tracker = ProgressTracker([StageSpec("ingest", has_items=True, expected_items=200)])
    tracker.start()
    tracker.begin_stage("ingest", expected_items=200)

    def worker() -> None:
        for _ in range(50):
            tracker.advance_item()
            tracker.set_percent(random.uniform(0.0, 100.0))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(tracker._samples) <= 32
    event = tracker.last_event_snapshot()
    assert event is not None
    assert 0.0 <= event.percent <= 100.0
    tracker.finish()
    assert tracker.last_event_snapshot() is not None


# ── ApplicationService 配置缓存 ───────────────────────────────────────────────


def _service(tmp_path: Path) -> ApplicationService:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"project: {{name: svc, workspace: '{tmp_path / 'work'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    return ApplicationService(config_path)


def test_config_cache_reuses_snapshot_and_reload_resets(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service._ensure_config()
    second = service._ensure_config()
    assert first is second
    service.reload()
    third = service._ensure_config()
    assert third is not first


def test_config_cache_concurrent_reads_no_error(tmp_path: Path) -> None:
    service = _service(tmp_path)
    errors: list[Exception] = []

    def reader() -> None:
        try:
            for _ in range(20):
                service._ensure_config()
        except Exception as exc:  # noqa: BLE001 - 测试内收集并发异常
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


# ── 回归 fixture gzip 上限 ────────────────────────────────────────────────────


def _regression_fixture(tmp_path: Path) -> RegressionLibrary:
    raw = copy.deepcopy(DEFAULTS)
    raw["extract"] = {"mode": "html", "fields": {"title": {"selector": "h1"}}}
    config = AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")
    response = FetchResult(
        CrawlRequest("https://example.org"), "https://example.org", 200,
        {"content-type": "text/html"}, b"<h1>Right</h1>", 0.1,
    )
    library = RegressionLibrary(config)
    library.capture(response, records=1, processor="html")
    return library


def test_regression_fixture_gzip_size_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library = _regression_fixture(tmp_path)
    monkeypatch.setattr(regression_library, "_MAX_FIXTURE_BODY_BYTES", 1)
    with pytest.raises(ValueError, match="大小上限"):
        library.load()


# ── 研究包 zip 流式打包 ───────────────────────────────────────────────────────


def test_research_package_streams_raw_file_into_zip(tmp_path: Path) -> None:
    raw = copy.deepcopy(DEFAULTS)
    config = AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")
    raw_dir = config.workspace / "raw"
    raw_dir.mkdir(parents=True)
    payload = b"hello research package " * 100
    (raw_dir / "note.txt").write_bytes(payload)

    target = tmp_path / "package.zip"
    result = create_research_package(config, target, include_raw=True)
    assert result["files"] >= 1

    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        raw_entries = [name for name in names if name.endswith("raw/note.txt")]
        assert raw_entries, names
        assert archive.read(raw_entries[0]) == payload
        assert "omnicrawler-package.json" in names
