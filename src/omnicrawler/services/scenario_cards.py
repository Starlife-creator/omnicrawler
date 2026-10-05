"""Bounded scenario metadata. Claims never grant installation or runtime trust."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

REQUIRED = ("problem", "inputs", "outputs", "permissions", "components", "offline", "host_version", "limitations")


def validate_card(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("scenario card requires schema_version 1")
    card: dict[str, Any] = {"schema_version": 1}
    for key in REQUIRED:
        text = value.get(key)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError(f"invalid scenario field: {key}")
        card[key] = text.strip()
    status = value.get("verification", "unverified")
    if status not in {"unverified", "fixture_verified", "site_verified"}:
        raise ValueError("invalid scenario verification")
    card["verification"] = status
    if status != "unverified":
        date.fromisoformat(value.get("verified_at", ""))
        evidence = value.get("evidence")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 20:
            raise ValueError("verified scenario requires evidence")
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError("invalid evidence")
            path, digest = item.get("path"), item.get("sha256")
            if not isinstance(path, str) or not path or len(path) > 500:
                raise ValueError("invalid evidence path")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid evidence digest")
        card["verified_at"] = value["verified_at"]
        card["evidence"] = evidence
    valid_for = value.get("valid_for_days", 90)
    if type(valid_for) is not int or not 1 <= valid_for <= 3650:
        raise ValueError("invalid scenario validity period")
    card["valid_for_days"] = valid_for
    parameters = value.get("parameters", {})
    if not isinstance(parameters, dict) or len(parameters) > 50 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) for key in parameters):
        raise ValueError("invalid scenario parameters")
    from ..templates.parameters import validate_parameters
    validate_parameters(parameters, {}, strict=False)
    card["parameters"] = parameters
    output_fields = value.get("output_fields", {})
    if not isinstance(output_fields, dict) or len(output_fields) > 100:
        raise ValueError("invalid scenario output fields")
    for name, kind in output_fields.items():
        if not isinstance(name, str) or not name or kind not in {"string", "number", "integer", "boolean", "array", "object"}:
            raise ValueError("invalid scenario output field type")
    card["output_fields"] = output_fields
    context = value.get("verification_context", {})
    if not isinstance(context, dict) or len(context) > 12 or any(not isinstance(item, str) or len(item) > 500 for item in context.values()):
        raise ValueError("invalid scenario verification context")
    card["verification_context"] = context
    return card


def assess_card(value: dict[str, Any], *, root: Path | None = None, today: date | None = None) -> dict[str, Any]:
    card = validate_card(value)
    report: dict[str, Any] = {"state": "unverified", "broken": False, "evidence_integrity": "not_checked"}
    if card["verification"] == "unverified":
        return report
    age = ((today or date.today()) - date.fromisoformat(card["verified_at"])).days
    report.update(age_days=age, state="future_date" if age < 0 else "stale" if age > card["valid_for_days"] else "within_declared_period")
    if root is not None:
        root = root.resolve()
        integrity = "matching"
        for item in card["evidence"]:
            relative = PurePosixPath(item["path"])
            if relative.is_absolute() or ".." in relative.parts or "\\" in item["path"] or ":" in item["path"]:
                integrity = "unsafe_path"
                break
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root):
                integrity = "unsafe_path"
                break
            try:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(64 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != item["sha256"]:
                    integrity = "mismatch"
                    break
            except OSError:
                integrity = "missing"
                break
        report["evidence_integrity"] = integrity
        if integrity != "matching":
            report["state"] = "evidence_invalid"
    return report


def describe_card(entry: dict[str, Any]) -> str:
    value = entry.get("scenario_card")
    if value is None:
        return "场景验收信息：未提供；请先试采并检查输出。"
    try:
        card = validate_card(value)
    except (ValueError, TypeError):
        return "场景验收信息：格式无效；不能据此判断适用性。"
    labels = {"problem": "解决问题", "inputs": "输入", "outputs": "输出", "permissions": "所需权限", "components": "所需组件", "offline": "离线条件", "host_version": "宿主版本", "limitations": "限制"}
    lines = [f"{label}：{card[key]}" for key, label in labels.items()]
    statuses = {"unverified": "未经验证", "fixture_verified": "本地固定样本验证", "site_verified": "指定站点验证"}
    lines.append("验证声明：" + statuses[card["verification"]] + (" / " + card["verified_at"] if "verified_at" in card else ""))
    assessment = assess_card(card)
    labels = {"unverified": "尚未验证", "stale": "证据已过期，需要复验；不能据此断言已失效",
              "future_date": "验证日期在未来，需要复核", "within_declared_period": "在声明有效期内，仍需当前参数试跑"}
    lines.append("当前适用性：" + labels[assessment["state"]])
    if card["parameters"]:
        lines.append("结构化参数：" + "、".join(card["parameters"]))
    if card["output_fields"]:
        lines.append("输出字段：" + "、".join(f"{name} ({kind})" for name, kind in card["output_fields"].items()))
    lines.append("此页未重新读取证据文件核验摘要。")
    lines.append("验证声明不替代签名、权限审核或目标站点试采。")
    return "\n".join(lines)
