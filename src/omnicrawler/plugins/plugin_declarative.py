"""Validation for market-safe, host-rendered declarative plugin views."""

from __future__ import annotations

import re
from typing import Any

MAX_VIEW_COMPONENTS = 64
MAX_COMPONENT_ITEMS = 500
MAX_TEXT_LENGTH = 512
VIEW_ZONES = frozenset({"left", "right", "bottom"})
COMPONENT_TYPES = frozenset(
    {"label", "button", "directory_picker", "slider", "select", "resource_list"}
)
_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,95}$")


def _text(value: Any, field: str, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"声明式视图 {field} 不能为空")
    if len(result) > MAX_TEXT_LENGTH:
        raise ValueError(f"声明式视图 {field} 过长")
    return result


def validate_view_descriptor(raw: Any) -> dict[str, Any]:
    """Return a normalized descriptor or reject unsupported UI semantics."""

    if not isinstance(raw, dict):
        raise ValueError("声明式视图必须是对象")
    allowed_top = {
        "view_id", "title", "preferred_zone", "movable", "resizable", "floatable",
        "default_width", "default_height", "minimum_width", "minimum_height", "components",
    }
    unknown = set(raw) - allowed_top
    if unknown:
        raise ValueError(f"声明式视图包含未知字段: {sorted(unknown)}")
    view_id = _text(raw.get("view_id"), "view_id", required=True).casefold()
    if _ID.fullmatch(view_id) is None:
        raise ValueError("声明式视图 view_id 格式非法")
    title = _text(raw.get("title"), "title", required=True)
    zone = _text(raw.get("preferred_zone", "right"), "preferred_zone").casefold()
    if zone not in VIEW_ZONES:
        raise ValueError(f"声明式视图区域不受支持: {zone}")
    components = raw.get("components", [])
    if not isinstance(components, list) or len(components) > MAX_VIEW_COMPONENTS:
        raise ValueError(f"声明式视图 components 必须是最多 {MAX_VIEW_COMPONENTS} 项的数组")
    normalized_components = [_validate_component(item) for item in components]
    component_ids = [item["id"] for item in normalized_components]
    if len(component_ids) != len(set(component_ids)):
        raise ValueError("声明式视图组件 id 不能重复")
    result: dict[str, Any] = {
        "view_id": view_id,
        "title": title,
        "preferred_zone": zone,
        "movable": bool(raw.get("movable", True)),
        "resizable": bool(raw.get("resizable", True)),
        "floatable": bool(raw.get("floatable", True)),
        "default_width": _bounded_int(raw.get("default_width", 380), 240, 1200, "default_width"),
        "default_height": _bounded_int(raw.get("default_height", 640), 160, 1200, "default_height"),
        "minimum_width": _bounded_int(raw.get("minimum_width", 240), 160, 1000, "minimum_width"),
        "minimum_height": _bounded_int(raw.get("minimum_height", 160), 120, 1000, "minimum_height"),
        "components": normalized_components,
    }
    return result


def _bounded_int(value: Any, minimum: int, maximum: int, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"声明式视图 {field} 必须是整数") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"声明式视图 {field} 超出范围 {minimum}..{maximum}")
    return result


def _validate_component(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("声明式视图组件必须是对象")
    kind = _text(raw.get("type"), "component.type", required=True).casefold()
    if kind not in COMPONENT_TYPES:
        raise ValueError(f"声明式视图组件类型不受支持: {kind}")
    component_id = _text(raw.get("id"), "component.id", required=True).casefold()
    if _ID.fullmatch(component_id) is None:
        raise ValueError("声明式视图组件 id 格式非法")
    allowed = {
        "type", "id", "label", "text", "action", "value", "minimum", "maximum",
        "options", "items", "empty_text", "directory_label",
        "discovery_kind", "discovery_id",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"声明式视图组件 {component_id} 包含未知字段: {sorted(unknown)}")
    result: dict[str, Any] = {"type": kind, "id": component_id}
    for field in ("label", "text", "action", "empty_text", "directory_label"):
        if field in raw:
            result[field] = _text(raw.get(field), f"component.{field}")
    if "action" in result and result["action"] and _ID.fullmatch(result["action"].casefold()) is None:
        raise ValueError(f"声明式视图组件 {component_id} action 格式非法")
    if kind == "directory_picker":
        discovery_kind = _text(raw.get("discovery_kind"), "component.discovery_kind")
        discovery_id = _text(raw.get("discovery_id"), "component.discovery_id")
        if bool(discovery_kind) != bool(discovery_id):
            raise ValueError("声明式目录选择器的 discovery_kind/discovery_id 必须同时提供")
        if discovery_kind not in {"", "steam_workshop"}:
            raise ValueError("声明式目录选择器使用了未知资源发现器")
        if discovery_kind:
            result.update({"discovery_kind": discovery_kind, "discovery_id": discovery_id})
    if kind == "slider":
        minimum = _bounded_int(raw.get("minimum", 0), -100_000, 100_000, "component.minimum")
        maximum = _bounded_int(raw.get("maximum", 100), -100_000, 100_000, "component.maximum")
        if minimum >= maximum:
            raise ValueError("声明式视图 slider minimum 必须小于 maximum")
        value = _bounded_int(raw.get("value", minimum), minimum, maximum, "component.value")
        result.update({"minimum": minimum, "maximum": maximum, "value": value})
    if kind == "select":
        options = raw.get("options", [])
        if not isinstance(options, list) or len(options) > 100:
            raise ValueError("声明式视图 select options 必须是最多 100 项的数组")
        if any(not isinstance(item, dict) for item in options):
            raise ValueError("声明式视图 select option 必须是对象")
        result["options"] = [
            {"label": _text(item.get("label"), "option.label", required=True), "value": _text(item.get("value"), "option.value")}
            for item in options
            if isinstance(item, dict)
        ]
        result["value"] = _text(raw.get("value"), "component.value")
    if kind == "resource_list":
        items = raw.get("items", [])
        if not isinstance(items, list) or len(items) > MAX_COMPONENT_ITEMS:
            raise ValueError(f"声明式视图 resource_list 最多允许 {MAX_COMPONENT_ITEMS} 项")
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("声明式视图 resource_list item 必须是对象")
            normalized_items.append(
                {
                    "id": _text(item.get("id"), "item.id", required=True),
                    "label": _text(item.get("label"), "item.label", required=True),
                    "subtitle": _text(item.get("subtitle"), "item.subtitle"),
                }
            )
        result["items"] = normalized_items
    return result
