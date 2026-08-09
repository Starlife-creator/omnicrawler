"""Transactional and partial template application with business-language diffs."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..core.utils import deep_merge
from .template_diff import diff_templates

_BUSINESS_NAMES = {
    "source": "入口与访问", "crawl": "采集范围", "extract": "字段内容", "selection": "主题筛选",
    "download": "附件下载", "processors": "PDF/OCR", "updates": "变化监测", "outputs": "导出结果",
    "plugins": "插件", "ai": "AI辅助", "resources": "资源预算",
}


@dataclass(frozen=True, slots=True)
class TemplateApplication:
    before: dict[str, Any]
    after: dict[str, Any]
    changes: tuple[dict[str, Any], ...]
    applied_sections: tuple[str, ...]

    def undo(self) -> dict[str, Any]:
        return copy.deepcopy(self.before)


def apply_template(
    current: dict[str, Any], template: dict[str, Any], sections: Iterable[str] | None = None,
) -> TemplateApplication:
    """Apply selected top-level sections; unspecified and unknown fields are preserved."""
    selected = tuple(dict.fromkeys(sections if sections is not None else template.keys()))
    patch = {key: copy.deepcopy(template[key]) for key in selected if key in template}
    before = copy.deepcopy(current)
    after = deep_merge(before, patch)
    changes = []
    for item in diff_templates(before, after):
        section = item["path"].split(".", 1)[0]
        item = copy.deepcopy(item)
        item["business_section"] = _BUSINESS_NAMES.get(section, section)
        item["business_change"] = {"added": "新增", "modified": "修改", "removed": "移除"}[item["change_type"]]
        changes.append(item)
    return TemplateApplication(before, after, tuple(changes), selected)

