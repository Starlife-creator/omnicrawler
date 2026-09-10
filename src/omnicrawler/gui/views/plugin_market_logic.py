"""插件市场面板的纯逻辑：目录条目解析、权限风险分级、版本兼容性裁决与安装审查文案。

从 plugin_market.py 迁出（P1-3 第一批）。无 Qt 依赖，可独立单测；
 heavyweight 依赖（plugin_broker / egress / AppConfig）保持函数内懒加载。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import __version__
from ...plugins.plugins import OFFICIAL_PLUGIN_TYPES
from ..i18n import _


def _project_root_of(base: str | Path | None) -> Path:
    if base:
        return Path(base)
    # src/omnicrawler/gui/views/plugin_market_logic.py -> 上溯 4 级到项目根（与原模块同目录）
    return Path(__file__).resolve().parents[4]


_CATALOG_PURPOSE = "plugin"

_TYPE_LABELS = {
    "source": _("数据源"),
    "fetcher": _("抓取器"),
    "processor": _("处理器"),
    "parser": _("解析器"),
    "extractor": _("提取器"),
    "auth_provider": _("认证"),
    "transformer": _("转换器"),
    "exporter": _("导出器"),
    "hook": _("生命周期"),
    "ui": _("原生界面"),
    "resource_provider": _("本地资源"),
    "view": _("声明式界面"),
}


def _entry_strings(entry: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = entry.get(key, [])
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _entry_plugin_types(entry: dict[str, Any]) -> tuple[str, ...]:
    """读取运行扩展点；旧 catalog 从 category/tags 做保守兼容推断。"""
    raw = entry.get("plugin_types")
    if isinstance(raw, (list, tuple)):
        values = [str(item).strip().casefold() for item in raw]
        return tuple(dict.fromkeys(item for item in values if item in OFFICIAL_PLUGIN_TYPES))
    candidates = [entry.get("category"), *_entry_strings(entry, "tags")]
    inferred = [str(item).strip().casefold() for item in candidates]
    return tuple(dict.fromkeys(item for item in inferred if item in OFFICIAL_PLUGIN_TYPES))


def _permission_risk(entry: dict[str, Any]) -> tuple[str, str]:
    permissions = {
        str(item).strip().casefold()
        for item in _entry_strings(entry, "permissions")
        if str(item).strip()
    }
    if (
        str(entry.get("execution_mode") or "subprocess") == "in_process"
        or permissions & {"secrets:read", "responses:payload", "render:scripted"}
    ):
        return "high", _("高风险")
    if permissions & {
        "network:scoped",
        "records:write",
        "responses:read",
        "artifacts:write",
        "files:read",
        "temp:write",
        "resources:read",
        "surfaces:background",
        "render:local",
    }:
        return "medium", _("需授权")
    return "low", _("低风险")


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(".") if part.isdigit())


def _compatibility(entry: dict[str, Any], current: str = __version__) -> tuple[str, str]:
    """覆盖市场现用的简单版本约束；无法判断时明确显示未知而不误拦截。"""
    from ...plugins.plugin_broker import validate_required_capabilities

    required_capabilities = entry.get("required_capabilities", {})
    if not isinstance(required_capabilities, dict):
        return "blocked", _("能力版本声明无效")
    try:
        validate_required_capabilities(required_capabilities)
    except ValueError as exc:
        return "blocked", _("宿主能力不兼容：") + str(exc)
    constraint = str(entry.get("compatible_core") or "").strip()
    if not constraint:
        return "unknown", _("兼容性未知")
    current_version = _version_tuple(current)
    if not current_version:
        return "unknown", _("兼容性未知")
    for clause in (item.strip() for item in constraint.split(",")):
        matched = False
        for operator in (">=", "<=", "==", ">", "<"):
            if not clause.startswith(operator):
                continue
            target = _version_tuple(clause[len(operator):].strip())
            if not target:
                return "unknown", _("兼容性未知")
            comparisons = {
                ">=": current_version >= target,
                "<=": current_version <= target,
                "==": current_version == target,
                ">": current_version > target,
                "<": current_version < target,
            }
            if not comparisons[operator]:
                return "incompatible", _("不兼容当前版本")
            matched = True
            break
        if not matched:
            return "unknown", _("兼容性未知")
    return "compatible", _("兼容")


def _install_block_reason(entry: dict[str, Any]) -> str:
    compatibility, detail = _compatibility(entry)
    if compatibility in {"incompatible", "blocked"}:
        return detail
    if "ui" in _entry_plugin_types(entry):
        return _("原生 UI 插件仅允许作为受信任本地插件使用，不能从市场安装")
    return ""


def _install_review_text(entry: dict[str, Any]) -> str:
    plugin_types = _entry_plugin_types(entry)
    type_text = ", ".join(_TYPE_LABELS.get(item, item) for item in plugin_types) or _("未知")
    mode = str(entry.get("execution_mode") or "subprocess")
    mode_text = _("隔离子进程") if mode == "subprocess" else _("进程内（高风险）")
    permissions = list(_entry_strings(entry, "permissions"))
    domains = list(_entry_strings(entry, "domains"))
    return _(
        "插件：{0}\n运行扩展点：{1}\n执行模式：{2}\n请求权限：{3}\n允许域名：{4}\n\n"
        "安装仅下载并验签；启用这些权限时仍需在项目插件管理中逐项批准。"
    ).format(
        entry.get("name") or entry.get("id") or "—",
        type_text,
        mode_text,
        ", ".join(permissions) if permissions else _("无"),
        ", ".join(domains) if domains else _("无"),
    )


def _market_egress(project_root: Path) -> Any:
    """Lazily build a shared EgressBroker for curated plugin-market traffic.

    The marketplace downloads third-party signed plugins; those requests must
    cross the same policy/budget/audit boundary as every other network egress,
    not ride a raw urllib call.  A fresh default broker is safe here because
    egress defaults only restrict private-network targets and count requests.
    """
    from ...core.config import DEFAULTS, AppConfig, deep_merge
    from ...security.egress import EgressBroker

    raw = deep_merge(dict(DEFAULTS), {"egress": {"audit": True}})
    raw.setdefault("project", {"name": "plugin-market", "workspace": str(project_root)})
    config = AppConfig(Path("<plugin-market>"), project_root, raw, project_root)
    return EgressBroker(config)
