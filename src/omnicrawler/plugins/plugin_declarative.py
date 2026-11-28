"""Validation for market-safe, host-rendered declarative plugin views."""

from __future__ import annotations

import re
from typing import Any

MAX_VIEW_COMPONENTS = 64
MAX_COMPONENT_ITEMS = 500
MAX_TEXT_LENGTH = 512
# 2026-09-24 P2.1：受限长文本组件（rich_text）——结构化文本段，宿主统一渲染。
# 威胁模型：内容来自不可信插件 ⇒ 不收 HTML、纯文本渲染、外链宿主确认后打开。
MAX_RICH_TEXT_SEGMENTS = 64
MAX_RICH_TEXT_TOTAL_CHARS = 4096
RICH_TEXT_SEGMENT_TYPES = frozenset({"heading", "paragraph", "bullet", "link"})
VIEW_ZONES = frozenset({"left", "right", "bottom"})

#: **浮层的 z 档位**（W6-B / W5-B 共用同一套模型）。
#:
#: ★ 2026-11-28：这张表原先只存在于 `gui/foreground_surface.py` —— 那个模块
#:   `import PySide6`，于是**契约校验层（本模块，纯数据、无 Qt）无法引用它**，
#:   想校验「插件声明的 z 档位合法吗」就只能在 GUI 侧做，或抄一份。
#:
#:   词表是**协议事实**，不是渲染细节 ⇒ 它属于这里。GUI 侧改为 import 本表。
#:
#: 三档的语义（都是**相对中央区**，与菜单/停靠面板/对话框无关）：
#:
#: - `underlay`：压在中央区内容**下面**（但仍在背景层之上）；
#: - `overlay`：压在中央区内容**上面**（默认）；
#: - `modal`：同上但带半透明遮罩，用于「正在处理」这类状态。
#:
#: **只有三个**，不是任意整数 —— 任意整数会让「谁在上」变成插件之间的暗战，
#: 而用户无法理解为什么某块面板忽然不见了。
FOREGROUND_Z_LEVELS: tuple[str, ...] = ("underlay", "overlay", "modal")

#: 浮层盖住哪一块（与 `surface.background.*` 的 scope 同词汇）。
FOREGROUND_SCOPES: tuple[str, ...] = ("application", "workspace", "canvas")

#: 浮层内容的形态白名单。**不接受任意色值/QSS/HTML** —— 只接受已渲染 PNG 帧
#: 或纯色 + 不透明度，与背景层同一条边界。
FOREGROUND_CONTENT_KINDS: tuple[str, ...] = ("rendered", "solid")
COMPONENT_TYPES = frozenset(
    {
        "label", "button", "directory_picker", "slider", "select", "resource_list",
        "rich_text", "text", "progress",
    }
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
        # ★ 2026-11-28（W6-B）：**在同一份描述符里**声明浮层，而不是另开一条能力。
        #
        #   为什么放在描述符里：浮层与面板是**同一个视图**的两部分（面板里的
        #   提示条/角标），分两次描述会让「浮层属于哪个视图」变得不可推断，
        #   卸载时也容易漏掉一半。
        #
        #   能力侧的 `surface.foreground.*` 仍然保留 —— 它服务于「运行期动态
        #   改浮层内容」（例如处理进度），而描述符里的这份是**初始声明**。
        "foreground",
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
        # ★ 2026-11-28：`minimum_*` 的上限从 1000 收紧到 480，并**额外要求**
        #   `minimum_* <= default_*`。
        #
        #   实测（offscreen，1366×768 笔记本尺寸）：每个 `QDockWidget` 的
        #   `minimumWidth` 都会**把主窗口撑宽**，因为侧栏 dock 与中央区共享同一条
        #   水平轴。两个插件面板各声明 `minimum_width=1000` ⇒ 窗口被强制拉到
        #   **2332px**，远超屏幕；用户既看不全也拖不动。
        #
        #   而 1000 这个上限本身没有依据 —— 一个侧栏面板占 1000px 已经是
        #   1920 屏幕的一半。480 是「仍有实际用途」与「不会撑爆窗口」的交点：
        #   两个 480 面板 + 最小中央区在 1366 宽下仍放得下。
        #
        #   `minimum <= default` 是 Qt 的硬约束（否则 `setMinimumSize` 会反过来
        #   把 `resize()` 的值抬上去），显式拒绝比让 Qt 静默纠正更诚实。
        "minimum_width": _bounded_int(raw.get("minimum_width", 240), 160, 480, "minimum_width"),
        "minimum_height": _bounded_int(raw.get("minimum_height", 160), 120, 480, "minimum_height"),
        "components": normalized_components,
    }
    # ★ W6-B：可选的浮层声明。未声明 ⇒ 不带该键（而不是 `None`），
    #   这样消费方 `if "foreground" in descriptor` 就够了。
    declared_foreground = _validate_foreground_declaration(raw.get("foreground"))
    if declared_foreground is not None:
        result["foreground"] = declared_foreground
    # 没有这条，Qt 会在 `setMinimumSize` 时把 `resize(default_*)` 静默抬到
    # `minimum_*` —— 插件声明的 `default` 就成了摆设，而作者无从得知。
    if result["minimum_width"] > result["default_width"]:
        raise ValueError(
            f"声明式视图 minimum_width（{result['minimum_width']}）"
            f"不能大于 default_width（{result['default_width']}）"
        )
    if result["minimum_height"] > result["default_height"]:
        raise ValueError(
            f"声明式视图 minimum_height（{result['minimum_height']}）"
            f"不能大于 default_height（{result['default_height']}）"
        )
    return result


#: 描述符里 `foreground` 允许的字段。**比 `surface.foreground.configure` 更窄**：
#: 描述符是**声明**（静态、随视图一起装载），只允许「初始状态」；
#: 运行期改动走能力，那里有更完整的词汇。
_FOREGROUND_DECLARATION_FIELDS = frozenset({"z", "scope", "color", "opacity", "render_handle"})


def _validate_foreground_declaration(raw: Any) -> dict[str, Any] | None:
    """校验描述符里的 `foreground` 段（W6-B）。

    **未声明就返回 None**（不是空字典）：调用方要能区分「没声明」与
    「声明了一个空浮层」—— 后者应判错（空浮层没有任何意义）。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("声明式视图 foreground 必须是对象")
    unknown = set(raw) - _FOREGROUND_DECLARATION_FIELDS
    if unknown:
        raise ValueError(f"声明式视图 foreground 包含未知字段: {sorted(unknown)}")
    if not raw:
        raise ValueError("声明式视图 foreground 不能是空对象（省略或给出至少一个字段）")
    result: dict[str, Any] = {}
    if "z" in raw:
        z = _text(raw.get("z"), "foreground.z").casefold()
        if z not in FOREGROUND_Z_LEVELS:
            allowed = "/".join(FOREGROUND_Z_LEVELS)
            raise ValueError(f"声明式视图 foreground.z 必须是 {allowed} 之一，收到 {z!r}")
        result["z"] = z
    if "scope" in raw:
        scope = _text(raw.get("scope"), "foreground.scope").casefold()
        if scope not in FOREGROUND_SCOPES:
            allowed = "/".join(FOREGROUND_SCOPES)
            raise ValueError(
                f"声明式视图 foreground.scope 必须是 {allowed} 之一，收到 {scope!r}"
            )
        result["scope"] = scope
    if "render_handle" in raw:
        result["render_handle"] = _text(
            raw.get("render_handle"), "foreground.render_handle", required=True
        )
    if "color" in raw:
        color = _text(raw.get("color"), "foreground.color", required=True)
        # 只做形态粗校验；真正的颜色合法性由 GUI 侧 QColor 判定（唯一权威）。
        # 这里只拒绝明显不是颜色的东西，避免把 HTML/QSS 注入放进来。
        if "<" in color or ";" in color:
            raise ValueError("声明式视图 foreground.color 不是颜色（不接受 HTML/QSS）")
        result["color"] = color
    if "opacity" in raw:
        result["opacity"] = _bounded_int(
            raw.get("opacity", 100), 0, 100, "foreground.opacity"
        )
    if "render_handle" in result and "color" in result:
        raise ValueError("声明式视图 foreground 不能同时给 render_handle 与 color")
    if "render_handle" not in result and "color" not in result:
        raise ValueError(
            "声明式视图 foreground 必须给出 render_handle 或 color（否则浮层没有内容）"
        )
    return result


def _bounded_int(value: Any, minimum: int, maximum: int, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"声明式视图 {field} 必须是整数") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"声明式视图 {field} 超出范围 {minimum}..{maximum}")
    return result


def _validate_rich_text_segments(raw: Any) -> list[dict[str, Any]]:
    """校验 rich_text 的结构化文本段（2026-09-24 P2.1）。

    段类型白名单：heading / paragraph / bullet / link；link 必须带 http(s) URL
    （打开前宿主还会弹确认）。总字符数受限，防止把宿主渲染面板撑成内存靶子。
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("声明式视图 rich_text segments 必须是非空数组")
    if len(raw) > MAX_RICH_TEXT_SEGMENTS:
        raise ValueError(f"声明式视图 rich_text segments 最多允许 {MAX_RICH_TEXT_SEGMENTS} 段")
    normalized: list[dict[str, Any]] = []
    total = 0
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("声明式视图 rich_text segment 必须是对象")
        unknown = set(item) - {"type", "text", "url"}
        if unknown:
            raise ValueError(f"声明式视图 rich_text segment 包含未知字段: {sorted(unknown)}")
        seg_type = _text(item.get("type"), "segment.type", required=True).casefold()
        if seg_type not in RICH_TEXT_SEGMENT_TYPES:
            raise ValueError(f"声明式视图 rich_text segment 类型不受支持: {seg_type}")
        text = _text(item.get("text"), "segment.text", required=(seg_type != "link"))
        if seg_type == "link":
            url = _text(item.get("url"), "segment.url", required=True)
            if not (url.startswith("https://") or url.startswith("http://")):
                raise ValueError("声明式视图 rich_text link 的 url 仅允许 http(s)")
        else:
            url = ""
        total += len(text) + len(url)
        if total > MAX_RICH_TEXT_TOTAL_CHARS:
            raise ValueError(
                f"声明式视图 rich_text 总字符数超出上限 {MAX_RICH_TEXT_TOTAL_CHARS}"
            )
        segment: dict[str, Any] = {"type": seg_type, "text": text}
        if url:
            segment["url"] = url
        normalized.append(segment)
    return normalized


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
        "discovery_kind", "discovery_id", "segments",
        # 2026-09-24 U4：单行文本输入（text 组件）的专有字段。
        "placeholder", "maxlength",
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
    if kind == "text":
        # 2026-09-24 U4：单行文本输入——插件面板可收集代理 URL / 登录页 URL 等配置。
        # 安全纪律与 select 同源：value 经 _text 限长（≤ MAX_TEXT_LENGTH）；
        # maxlength 只是 GUI 侧 QLineEdit.setMaxLength 的输入上限（1..512），
        # 防止把宿主面板当无界输入的内存靶子。未知字段已在上方统一拒绝。
        result["value"] = _text(raw.get("value"), "component.value")
        placeholder = _text(raw.get("placeholder"), "component.placeholder")
        if placeholder:
            result["placeholder"] = placeholder
        result["maxlength"] = _bounded_int(
            raw.get("maxlength", MAX_TEXT_LENGTH), 1, MAX_TEXT_LENGTH, "component.maxlength"
        )
    if kind == "progress":
        # 2026-09-24 U6：运行进度区——数值不经描述符携带，只由 view.progress
        # 推送刷新，故字段面收紧到 type/id/label（比通用白名单更严），
        # 防止插件把静态数值/动作伪装进进度区。
        extra = set(raw) - {"type", "id", "label"}
        if extra:
            raise ValueError(f"声明式视图进度组件 {component_id} 包含未知字段: {sorted(extra)}")
    if kind == "rich_text":
        result["segments"] = _validate_rich_text_segments(raw.get("segments"))
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
