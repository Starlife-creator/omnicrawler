from __future__ import annotations

import pytest

from omnicrawl.core.config import DEFAULTS, AppConfig
from omnicrawl.core.models import CrawlRequest
from omnicrawl.services.record_sinks import RecordSinkManager, build_record_sink_manager


class _FailingSink:
    def write(self, *_args) -> int:
        raise RuntimeError("temporarily unavailable")

    def close(self) -> None:
        raise RuntimeError("still unavailable")


def test_sink_errors_are_bounded_but_counts_remain_complete() -> None:
    manager = RecordSinkManager(sinks=[("mirror", _FailingSink())], max_errors=2)
    request = CrawlRequest("https://example.com")
    for _ in range(3):
        manager.write("run", request, [])
    manager.close()

    status = manager.status()
    assert status["error_counts"] == {"mirror": 4}
    assert len(status["recent_errors"]) == 2
    assert {item["sink"] for item in status["recent_errors"]} == {"mirror"}


def test_negative_storage_error_limit_is_rejected(tmp_path) -> None:
    config = AppConfig(
        tmp_path / "task.yaml", tmp_path,
        {
            **DEFAULTS,
            "project": {"name": "test", "workspace": str(tmp_path / "work")},
            "storage": {"records": {"backends": [], "max_errors": -1}},
        },
        tmp_path / "work",
    )
    with pytest.raises(ValueError, match="max_errors"):
        build_record_sink_manager(config)
