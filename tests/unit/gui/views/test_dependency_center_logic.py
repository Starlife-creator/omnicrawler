"""「环境与依赖」面板的**纯逻辑**测试（决策四 P1）。

只测纯函数：把 capability_report 的分级结果摊平成行、挑出可安装的缺失行、
渲染只读徽标。Qt 对话框本身不在此测（需 offscreen，另由 GUI 套件覆盖）。
"""

from __future__ import annotations

from omnicrawler.gui.views.dependency_center import (
    CapabilityRow,
    capability_rows,
    missing_installable_rows,
    readiness_badge,
)


def _report() -> dict:
    return {
        "standard": {
            "items": {
                "core_yaml": {"name": "YAML", "ready": True, "description": "配置解析", "tier": "standard"},
                "pdf_text": {"name": "PDF", "ready": False, "description": "PDF 解析", "tier": "standard"},
            }
        },
        "full": {
            "items": {
                "tesseract_ocr": {"name": "Tesseract", "ready": False, "description": "OCR 引擎", "tier": "full"},
            }
        },
        "optional": {
            "items": {
                "redis": {"name": "Redis", "ready": False, "description": "分布式队列", "tier": "optional"},
            }
        },
    }


class TestCapabilityRows:
    def test_flattens_all_tiers_in_order(self) -> None:
        rows = capability_rows(_report())
        assert [row.key for row in rows] == ["core_yaml", "pdf_text", "tesseract_ocr", "redis"]

    def test_requirement_only_for_installable(self) -> None:
        rows = {row.key: row for row in capability_rows(_report())}
        # pdf_text 有 pip 路径；tesseract_ocr（系统级）没有
        assert rows["pdf_text"].requirement == "pdfplumber"
        assert rows["tesseract_ocr"].requirement == ""

    def test_empty_report(self) -> None:
        assert capability_rows({}) == []

    def test_non_dict_items_skipped(self) -> None:
        report = {"standard": {"items": {"broken": "not-a-dict", "ok": {"name": "X", "ready": True}}}}
        rows = capability_rows(report)
        assert [row.key for row in rows] == ["ok"]


class TestMissingInstallable:
    def test_only_missing_with_requirement(self) -> None:
        rows = capability_rows(_report())
        missing = missing_installable_rows(rows)
        assert [row.requirement for row in missing] == ["pdfplumber"]

    def test_ready_rows_excluded(self) -> None:
        rows = [
            CapabilityRow(key="a", name="A", tier="standard", ready=True, requirement="a-pkg"),
            CapabilityRow(key="b", name="B", tier="standard", ready=False, requirement="b-pkg"),
        ]
        assert [row.key for row in missing_installable_rows(rows)] == ["b"]


class TestReadinessBadge:
    def test_ready(self) -> None:
        row = CapabilityRow(key="a", name="A", tier="standard", ready=True)
        assert readiness_badge(row) == "[就绪]"

    def test_missing_installable(self) -> None:
        row = CapabilityRow(key="a", name="A", tier="standard", ready=False, requirement="pkg")
        assert readiness_badge(row) == "[缺失·可安装]"

    def test_missing_manual(self) -> None:
        row = CapabilityRow(key="a", name="A", tier="full", ready=False)
        assert readiness_badge(row) == "[缺失·需手动]"
