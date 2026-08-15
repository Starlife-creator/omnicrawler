"""Transactional and partial template application with business-language diffs."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..core.utils import deep_merge
from .template_diff import diff_templates

# B11-006 / B05-009：安全键只允许更严方向；apply_template 合并后回盖为用户既有安全值，
# 防止模板（即使签名）把 respect_robots/allow_private_network/verify_tls 等翻转成宽松。
_SAFE_HTTP_KEYS = ("respect_robots", "allow_private_network", "verify_tls", "allow_unintercepted_selenium")
_SAFE_EGRESS_KEYS = ("enabled",)

_BUSINESS_NAMES = {
    "source": "入口与访问", "crawl": "采集范围", "extract": "字段内容", "selection": "主题筛选",
    "download": "附件下载", "processors": "PDF/OCR", "updates": "变化监测", "outputs": "导出结果",
    "plugins": "插件", "ai": "AI辅助", "resources": "资源预算",
}


def _restore_safe_baseline(after: dict[str, Any], before: dict[str, Any]) -> None:
    """合并后把安全键回盖为用户既有值（模板段不得翻转安全方向）。"""
    http_before = before.get("http")
    http_after = after.get("http")
    if isinstance(http_before, dict) and isinstance(http_after, dict):
        for key in _SAFE_HTTP_KEYS:
            if key in http_before and key in http_after:
                http_after[key] = http_before[key]
    egress_before = before.get("egress")
    egress_after = after.get("egress")
    if isinstance(egress_before, dict) and isinstance(egress_after, dict):
        for key in _SAFE_EGRESS_KEYS:
            if key in egress_before and key in egress_after:
                egress_after[key] = egress_before[key]


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
    # B11-006：安全键不被模板段覆盖（回盖用户既有安全值），diff 也随之收敛。
    _restore_safe_baseline(after, before)
    changes = []
    for item in diff_templates(before, after):
        section = item["path"].split(".", 1)[0]
        item = copy.deepcopy(item)
        item["business_section"] = _BUSINESS_NAMES.get(section, section)
        item["business_change"] = {"added": "新增", "modified": "修改", "removed": "移除"}[item["change_type"]]
        changes.append(item)
    return TemplateApplication(before, after, tuple(changes), selected)

