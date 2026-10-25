"""B-P3-3：fail-open 静默吞异常路径必须留痕（日志 + exc_info），不中断主流程。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

import omnicrawler.services.gene_maintenance as gene_maintenance
import omnicrawler.services.metrics as metrics_module
from omnicrawler.quality.gene_pool import GenePool


class _FakeStore:
    def top_genes(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []


def test_gene_maintenance_failure_warns_and_still_recommends(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_store: Any) -> None:
        raise RuntimeError("基因维护服务临时不可用")

    monkeypatch.setattr(gene_maintenance, "maybe_maintain", _boom)

    pool = GenePool(_FakeStore())
    with caplog.at_level(logging.WARNING, logger="omnicrawler.quality.gene_pool"):
        genes = pool.recommend("scene", "slot")

    assert genes == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "维护失败必须留 warning 痕迹，不得静默 pass"
    assert warnings[0].exc_info is not None


def test_structure_fingerprint_failure_debug_logged_and_non_raising(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("指纹计算失败")

    monkeypatch.setattr(metrics_module, "structure_fingerprint", _boom)

    metrics = metrics_module.RunMetrics()
    record = SimpleNamespace(data={"title": "x"}, record_type="html", source_url="https://example.org/")

    with caplog.at_level(logging.DEBUG, logger=metrics_module.LOGGER.name):
        metrics.record_extracted(record)  # 不得抛出

    debug = [r for r in caplog.records if r.levelno == logging.DEBUG and "结构指纹" in r.message]
    assert debug, "观测层异常必须留 debug + exc_info 痕迹"
    assert debug[0].exc_info is not None
