"""A-P3-5：集中阈值 / 复核权重配置校验与复核评分权重接线回归测试。"""
from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import AppConfig, DEFAULTS, deep_merge, validate_config
from omnicrawler.review import review_workbench
from omnicrawler.review.review_workbench import ReviewItem


def _config(**overrides: object) -> object:
    raw = deep_merge(DEFAULTS, {
        "project": {"name": "tuning", "workspace": "work"},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 1, "max_depth": 0, "same_host": True},
        "http": {"user_agent": "TuningTest/1.0", "respect_robots": False, "delay_seconds": 0},
        **overrides,
    })
    return AppConfig(Path("<m>"), Path.cwd(), raw, Path.cwd())


def test_default_tuning_section_produces_no_validation_errors() -> None:
    errors, _warnings = validate_config(_config())
    assert errors == []


def test_negative_review_weight_is_rejected() -> None:
    errors, _ = validate_config(_config(tuning={"review_weights": {"missing_required": -5}}))
    assert any("review_weights.missing_required" in message for message in errors)


def test_poll_interval_range_is_validated() -> None:
    errors, _ = validate_config(_config(tuning={
        "run_control_poll_min_seconds": 1.0,
        "run_control_poll_max_seconds": 0.05,
    }))
    assert any("run_control_poll" in message for message in errors)


def test_review_risk_score_uses_configured_weights(monkeypatch) -> None:
    monkeypatch.setattr(review_workbench, "_REVIEW_WEIGHTS", dict(DEFAULTS["tuning"]["review_weights"]))
    item = ReviewItem(
        record_id="r1", source_url="http://x", fields=[],
        missing_required=("title",), rule_conflicts=1,
    )
    defaults = DEFAULTS["tuning"]["review_weights"]
    expected = round(defaults["missing_required"] + defaults["rule_conflict"], 3)
    assert item.risk_score == expected

    review_workbench.configure_review_weights({"missing_required": 100})
    assert item.risk_score == round(100 + defaults["rule_conflict"], 3)
