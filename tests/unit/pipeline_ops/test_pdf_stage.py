"""PDF 阶段定义的契约测试：启用判据、指标、检查点、回调前缀、取消语义。

这些断言的价值在于「阶段被搬出编排层后语义没变」——所以既要正向覆盖四种启用组合，
也要用反向断言证明取消/未启用时**确实什么都没做**（不写检查点、不记指标）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.pipeline_ops import pdf_stage as stage


class _Metrics:
    def __init__(self) -> None:
        self.gauges: dict[str, float] = {}
        self.stages: dict[str, float] = {}

    def gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def record_stage(self, name: str, seconds: float) -> None:
        self.stages[name] = seconds


class _State:
    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, str, str, dict[str, Any]]] = []

    def save_checkpoint(self, run_id: str, stage_name: str, key: str, payload: dict[str, Any]) -> None:
        self.checkpoints.append((run_id, stage_name, key, payload))


def _config(tmp_path: Path, *, pdf_enabled: bool) -> AppConfig:
    """最小 AppConfig：只填 ``processors.pdf.enabled``，其余走 DEFAULTS 兜底。"""
    raw = {"processors": {"pdf": {"enabled": pdf_enabled}}}
    return AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")


def test_enabled_follows_config_when_no_override(tmp_path: Path) -> None:
    assert stage.pdf_stage_enabled(_config(tmp_path, pdf_enabled=True), None) is True
    assert stage.pdf_stage_enabled(_config(tmp_path, pdf_enabled=False), None) is False


def test_explicit_override_wins_over_config(tmp_path: Path) -> None:
    """run_pdf 的 True/False 必须压过配置——workbench 就靠 False 跳过 PDF 阶段。"""
    assert stage.pdf_stage_enabled(_config(tmp_path, pdf_enabled=False), True) is True
    assert stage.pdf_stage_enabled(_config(tmp_path, pdf_enabled=True), False) is False


def test_stage_runs_and_records_gauges_and_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metrics, state = _Metrics(), _State()
    seen_callbacks: list[tuple[str, Any]] = []

    def fake_pipeline(config, state_store, *, run_id=None, callback=None, should_stop=None):
        if callback is not None:
            callback("extract", {"pages": 2})
        return {
            "enabled": True,
            "documents": 3,
            "result": {"processed": 3, "succeeded": 2, "failed": 1, "ocr_pages": 4, "pages": 9},
        }

    monkeypatch.setattr(stage, "run_pdf_pipeline", fake_pipeline)
    summary = stage.run_pdf_stage(
        _config(tmp_path, pdf_enabled=True),
        state,  # type: ignore[arg-type]
        run_id="r1",
        callback=lambda event, value: seen_callbacks.append((event, value)),
        metrics=metrics,
    )

    assert summary is not None and summary["documents"] == 3
    assert seen_callbacks == [("pdf_extract", {"pages": 2})]
    assert metrics.gauges == {
        "omnicrawler_pdf_documents": 3.0,
        "omnicrawler_pdf_processed": 3.0,
        "omnicrawler_pdf_succeeded": 2.0,
        "omnicrawler_pdf_failed": 1.0,
        "omnicrawler_pdf_ocr_pages": 4.0,
        "omnicrawler_pdf_pages": 9.0,
    }
    assert [c[1] for c in state.checkpoints] == ["pdf"]
    assert "pdf" in metrics.stages


def test_disabled_stage_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """反向断言：未启用时不得写检查点、不得记指标、不得调 pipeline。"""
    metrics, state = _Metrics(), _State()
    called: list[str] = []
    monkeypatch.setattr(stage, "run_pdf_pipeline", lambda *a, **k: called.append("ran") or {})
    assert (
        stage.run_pdf_stage(
            _config(tmp_path, pdf_enabled=False),
            state,  # type: ignore[arg-type]
            run_id="r1",
            metrics=metrics,
        )
        is None
    )
    assert called == []
    assert state.checkpoints == []
    assert metrics.gauges == {}


def test_cancelled_run_skips_the_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """取消时不跑：已落盘附件不值得在用户点了停止之后再读一遍。"""
    state = _State()
    called: list[str] = []
    monkeypatch.setattr(stage, "run_pdf_pipeline", lambda *a, **k: called.append("ran") or {})
    assert (
        stage.run_pdf_stage(
            _config(tmp_path, pdf_enabled=True),
            state,  # type: ignore[arg-type]
            run_id="r1",
            status="cancelled",
        )
        is None
    )
    assert called == []
    assert state.checkpoints == []


def test_partial_success_still_runs_the_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """只有 cancelled 跳过；partial_success 仍要处理已下载的附件。"""
    state = _State()
    monkeypatch.setattr(
        stage, "run_pdf_pipeline", lambda *a, **k: {"enabled": True, "documents": 1}
    )
    summary = stage.run_pdf_stage(
        _config(tmp_path, pdf_enabled=True),
        state,  # type: ignore[arg-type]
        run_id="r1",
        status="partial_success",
    )
    assert summary is not None
    assert [c[1] for c in state.checkpoints] == ["pdf"]


def test_metrics_are_optional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 metrics 时仍要落检查点——检查点是恢复的依据，指标只是观测。"""
    state = _State()
    monkeypatch.setattr(
        stage, "run_pdf_pipeline", lambda *a, **k: {"enabled": True, "documents": 0}
    )
    stage.run_pdf_stage(
        _config(tmp_path, pdf_enabled=True),
        state,  # type: ignore[arg-type]
        run_id="r1",
        metrics=None,
    )
    assert len(state.checkpoints) == 1
