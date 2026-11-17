"""PDF 工作台的纯逻辑：结果失败项归集与内置模板目录。

从 pdf_workbench.py 迁出（P1-3 第一批）。无 Qt 依赖，可独立单测
（tests/unit/pdf/test_pdf_fault_tolerance.py 直接导入 ``_collect_failures``）。
"""
from __future__ import annotations

from ..i18n import _


def _collect_failures(result: object) -> list[str]:
    """S2.3.4：递归收集所有阶段的 failed/stopped 标志（含 run_extraction 的 processing 嵌套）。"""
    failures: list[str] = []
    if not isinstance(result, dict):
        return failures
    for key, value in result.items():
        if key == "status":
            continue
        if isinstance(value, dict):
            if value.get("failed"):
                failures.append(str(value.get("error") or _(f"{key} 阶段失败")))
            else:
                failures.extend(_collect_failures(value))
    if result.get("stopped"):
        failures.append(_("管线已停止（用户取消或前序阶段失败）"))
    return failures


# ── PDF 模板定义 ────────────────────────────────────────────────
_PDF_TEMPLATES: list[dict[str, str]] = [
    {
        "id": "builtin:pdf/generic_template.yaml",
        "name": _("泛用 PDF 模板"),
        "desc": _("通用字段抽取模板，适合合同、年报、论文等"),
    },
    {
        "id": "builtin:pdf/announcement_fields.yaml",
        "name": _("公告 PDF 模板"),
        "desc": _("公告/公示类文档字段抽取，含标题、日期、正文等"),
    },
]
