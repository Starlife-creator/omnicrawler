"""Validate explicit, site-declared authentication response checks."""

from __future__ import annotations

from typing import Any


def validate_auth_check(value: Any) -> list[str]:
    if value is None or value == {}:
        return []
    if not isinstance(value, dict) or set(value) - {"status_codes", "selector", "redirect_paths"}:
        return ["source.auth_check 必须是认证检查声明，仅支持 status_codes/selector/redirect_paths"]
    errors = []
    if "status_codes" in value and (
        not isinstance(value["status_codes"], list) or not value["status_codes"]
        or any(type(item) is not int or item not in {401, 403} for item in value["status_codes"])
    ):
        errors.append("source.auth_check.status_codes 须为明确声明的 401/403 列表")
    if "selector" in value and (not isinstance(value["selector"], str) or not value["selector"].strip()):
        errors.append("source.auth_check.selector 须为非空 CSS 选择器")
    if "redirect_paths" in value and (
        not isinstance(value["redirect_paths"], list) or not value["redirect_paths"]
        or any(not isinstance(item, str) or not item.startswith("/") or "?" in item or "#" in item for item in value["redirect_paths"])
    ):
        errors.append("source.auth_check.redirect_paths 须为精确登录路径列表")
    return errors
