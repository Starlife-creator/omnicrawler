"""C-#9：``gui/views/pdf_workbench_logic.py`` 纯逻辑单测（无 Qt 依赖）。

``_collect_failures`` 此前仅经 ``pdf_workbench`` 视图层间接覆盖；本文件直接
锁定逻辑模块自身的递归归集语义与内置模板清单不变量。
"""

from __future__ import annotations

from omnicrawler.gui.views.pdf_workbench_logic import _PDF_TEMPLATES, _collect_failures


def test_collect_failures_non_dict_returns_empty() -> None:
    assert _collect_failures(None) == []
    assert _collect_failures("not-a-dict") == []
    assert _collect_failures(42) == []


def test_collect_failures_skips_status_key() -> None:
    assert _collect_failures({"status": {"documents": {}}}) == []


def test_collect_failures_nested_failed_stage_collects_error() -> None:
    result = {
        "processing": {
            "ingest": {"new": 1},
            "ocr": {"failed": True, "error": "依赖缺失"},
        },
        "extract": {"records": 0},
    }
    failures = _collect_failures(result)
    assert failures == ["依赖缺失"]


def test_collect_failures_stopped_appends_cancel_message() -> None:
    failures = _collect_failures({"stopped": True})
    assert len(failures) == 1
    assert "停止" in failures[0]


def test_collect_failures_fallback_message_when_error_missing() -> None:
    failures = _collect_failures({"ocr": {"failed": True}})
    assert failures and "ocr" in failures[0]


def test_builtin_templates_are_unique_and_labeled() -> None:
    ids = [template["id"] for template in _PDF_TEMPLATES]
    assert len(ids) == len(set(ids))
    for template in _PDF_TEMPLATES:
        assert template["id"].startswith("builtin:")
        assert template["name"] and template["desc"]
