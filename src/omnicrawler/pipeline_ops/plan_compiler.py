from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .task_ir import TASK_IR_VERSION, TaskIR, source_domains


@dataclass(frozen=True, slots=True)
class TaskPlan:
    plan_version: int
    ir: dict[str, Any]
    config: dict[str, Any]
    capabilities: tuple[str, ...]
    permissions: dict[str, Any]
    estimates: dict[str, Any]
    conflicts: tuple[str, ...]
    warnings: tuple[str, ...]
    explanation: tuple[str, ...]
    plan_hash: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version, "ir": copy.deepcopy(self.ir),
            "config": copy.deepcopy(self.config), "capabilities": list(self.capabilities),
            "permissions": copy.deepcopy(self.permissions), "estimates": copy.deepcopy(self.estimates),
            "conflicts": list(self.conflicts), "warnings": list(self.warnings),
            "explanation": list(self.explanation), "plan_hash": self.plan_hash,
        }


def compile_task_plan(ir: TaskIR, *, available_capabilities: Iterable[str] | None = None) -> TaskPlan:
    config = ir.to_config()
    seeds = [str(item) for item in ir.source.get("seeds", [])]
    conflicts: list[str] = []
    warnings: list[str] = []
    if ir.ir_version != TASK_IR_VERSION:
        conflicts.append(f"不支持的Task IR版本: {ir.ir_version}")
    if not seeds:
        conflicts.append("至少需要一个数据源入口")
    required = tuple(sorted(set(ir.capabilities)))
    if available_capabilities is not None:
        missing = sorted(set(required) - set(available_capabilities))
        if missing:
            conflicts.append("缺少运行能力: " + ", ".join(missing))
    domains = source_domains(ir)
    egress = config.get("egress", {}) if isinstance(config.get("egress"), dict) else {}
    allowed = {str(item).casefold() for item in egress.get("allowed_domains", [])}
    if allowed and any(not any(domain == item or domain.endswith("." + item) for item in allowed) for domain in domains):
        conflicts.append("入口域名与egress.allowed_domains冲突")
    pages = max(1, int(ir.scope.get("max_pages", config.get("crawl", {}).get("max_pages", 100))))
    per_response = int(config.get("http", {}).get("max_response_bytes", 50_000_000))
    estimates = {"maximum_pages": pages, "estimated_requests": pages, "upper_bound_bytes": pages * per_response}
    auth = bool(ir.authorization.get("provider") or ir.authorization.get("options") or ir.authorization.get("url"))
    ai = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    storage = config.get("storage", {}) if isinstance(config.get("storage"), dict) else {}
    permissions = {
        "network_domains": domains,
        "credentials": {"required": auth, "domains": egress.get("credential_domains", domains if auth else [])},
        "ai": {"enabled": ai.get("mode", "disabled") != "disabled", "providers": sorted((ai.get("providers") or {}).keys())},
        "components": list(required),
        "storage": storage,
    }
    explanation = [f"从{len(seeds)}个入口开始，最多处理{pages}个页面。"]
    if domains:
        explanation.append("仅访问任务声明的域名边界：" + "、".join(domains) + "。")
    if "browser" in required:
        explanation.append("使用浏览器执行动态渲染或已录制动作，并检查每个可见子请求。")
    if ir.attachments.get("enabled"):
        explanation.append("下载获准类型的附件；PDF按任务配置进入文本提取或OCR。")
    if ai.get("mode", "disabled") != "disabled":
        explanation.append("AI只执行声明的增强步骤，网络和费用仍受任务预算约束。")
    if conflicts:
        warnings.append("计划存在冲突，解决前不能正式运行。")
    payload = {
        "plan_version": 1, "ir": _redact_for_hash(ir.to_mapping()), "config": _redact_for_hash(config),
        "capabilities": required, "permissions": _redact_for_hash(permissions), "estimates": estimates,
        "conflicts": conflicts, "warnings": warnings, "explanation": explanation,
    }
    plan_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return TaskPlan(1, ir.to_mapping(), config, required, permissions, estimates, tuple(conflicts), tuple(warnings), tuple(explanation), plan_hash)


def diff_plans(before: TaskPlan, after: TaskPlan) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    _diff("", before.to_mapping(), after.to_mapping(), changes)
    return changes


def _diff(path: str, before: Any, after: Any, changes: list[dict[str, Any]]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            _diff(f"{path}.{key}".lstrip("."), before.get(key), after.get(key), changes)
    elif before != after:
        changes.append({"path": path, "before": before, "after": after})


_REDACT_TOKENS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "bearer", "cookie",
    "密码", "口令", "密钥", "令牌", "凭据", "授权",
)


def _redact_for_hash(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: ("<redacted>" if any(token in str(key).casefold() for token in _REDACT_TOKENS) else _redact_for_hash(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_for_hash(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_for_hash(item) for item in value)
    return value
