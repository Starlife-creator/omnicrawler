"""Bounded scenario metadata. Claims never grant installation or runtime trust."""
from __future__ import annotations

from datetime import date
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
    return card


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
    lines.append("验证声明不替代签名、权限审核或目标站点试采。")
    return "\n".join(lines)
