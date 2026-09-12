"""质量基准的端到端 ratchet：在真实流水线上跑内置任务，四项口径必须满分。

这是「可复现的任务基准」落地的证据：任务页面、抽取规则与期望结果都是代码里的常量，
全程离线（本地 HTTP 服务），因此**同一版本跑出同一结果**。
任何让数据变得不完整、不准确或丢失来源证据的改动，都会在这里失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.services.quality_benchmark import TASKS, run_task


@pytest.mark.parametrize("task", TASKS, ids=[task.name for task in TASKS])
def test_builtin_task_scores_full_marks(task, tmp_path: Path) -> None:
    score = run_task(task, workdir=tmp_path / task.name)

    assert score.completeness == 1.0, f"完整性不足：采到 {score.found_records} 条，期望 {score.expected_records} 条"
    assert score.accuracy == 1.0, f"准确性不足：{score.matched_records} 条记录的字段与真值不符"
    assert score.evidence_ratio == 1.0, "有记录缺少 source_url（来源证据）"
    assert score.mean_field_completeness == 1.0, "流水线自报的字段完整度未达 1.0"
    assert score.unexpected_records == 0, "交付了任务真值之外的记录"
    assert score.duplicate_records == 0, "同一业务记录被重复交付"
    assert score.reported_completeness_violations == 0, "字段自报完整度与实际字段存在性矛盾"
    assert score.ok is True


@pytest.mark.parametrize("task", TASKS, ids=[task.name for task in TASKS])
def test_result_carries_comparability_metadata(task, tmp_path: Path) -> None:
    """结果须自带可比信息：配置指纹 + 环境（否则两次结果不能证明可比）。"""
    score = run_task(task, workdir=tmp_path / task.name)

    assert score.config_sha256, "缺少生成配置的指纹"
    environment = dict(score.environment)
    assert environment.get("package_version"), "缺少包版本"
    assert environment.get("python"), "缺少 Python 版本"
    assert environment.get("platform"), "缺少平台"
