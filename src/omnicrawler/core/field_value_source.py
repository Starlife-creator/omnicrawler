"""字段「取值位置」的唯一真源（W2.2 / §5.3 #9 收口）。

## 为什么需要它（2026-09-15 核读的真实状态）

1. **同一件事两个键名**：运行期配置写 ``extract.fields.<name>.attr``
   （``extraction/intelligent_scraper.py`` 写出、``extraction/extractors.py`` 读），
   而 GUI / 页面分析器 / easyspider 桥 / 视觉点选都叫 ``attribute``。
2. **GUI 表达不了合法配置**：引擎**支持空选择器**取「条目元素自身」——
   ``extractors.py`` 里就是 ``nodes = select_nodes(context, selector) if selector else [context]``
   —— 但 ``gui/core/config_model.py::FieldDef.validate(require_selector=True)`` 直接报
   「选择器不能为空」⇒「取本条记录自己的 href」这种规则**在表单里建不出来**（GUI 与核心契约不一致）。

## 契约（与引擎逐条对齐，见 ``tests/unit/core/test_field_value_source_contract.py``）

| position | 运行期形状 | 引擎行为 |
|---|---|---|
| ``child`` | ``selector`` 非空（``attr`` 可选） | 先选子元素，再取其 ``attr``；没有 ``attr`` 就取文本 |
| ``element`` | ``selector: ""``、无 ``attr`` | 取**条目元素自身**的文本 |
| ``element_attr`` | ``selector: ""``、``attr: <名字>`` | 取**条目元素自身**的那个属性 |

★ **配置里不写 position**：由 ``selector`` / ``attr`` 推导（:func:`position_of`）⇒ 旧配置**零迁移**、
引擎行为一字不改；本契约只负责「GUI 怎么表达 · 两侧怎么校验 · 键名怎么归一」。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: 运行期配置里的键名（**唯一真源**：§5.3 #9 之前散在 GUI 与引擎两处）。
SELECTOR_KEY = "selector"
ATTR_KEY = "attr"

#: GUI / 页面分析器 / easyspider 桥 / 视觉点选历史上的键名；由 :func:`normalize_rule` 收敛到 ``attr``。
ATTRIBUTE_ALIAS = "attribute"

POSITION_CHILD = "child"
POSITION_ELEMENT = "element"
POSITION_ELEMENT_ATTR = "element_attr"


@dataclass(frozen=True)
class FieldPosition:
    """一种取值位置（名字是结构约定；中文标签属表现层，不放这里）。"""

    key: str
    needs_selector: bool
    needs_attribute: bool
    summary: str


#: 全部取值位置（顺序即 GUI 下拉里的顺序）。
POSITIONS: tuple[FieldPosition, ...] = (
    FieldPosition(POSITION_CHILD, True, False, "在条目内选子元素取值（选择器必填）"),
    FieldPosition(POSITION_ELEMENT, False, False, "取条目元素自身的文本（选择器留空、不给属性）"),
    FieldPosition(POSITION_ELEMENT_ATTR, False, True, "取条目元素自身的属性（选择器留空、给属性名）"),
)

POSITION_BY_KEY: dict[str, FieldPosition] = {position.key: position for position in POSITIONS}

#: 常见「值载体」属性：GUI 下拉的候选集（**不是**校验白名单——引擎允许任意属性名，
#: 例如自定义 ``data-*`` 也可能正是要采的值，硬拦会误伤合法配置）。
COMMON_ATTRIBUTES: tuple[str, ...] = (
    "href",
    "src",
    "text",
    "datetime",
    "title",
    "alt",
    "content",
    "value",
    "data-src",
)


def position_of(rule: Mapping[str, Any]) -> FieldPosition:
    """从运行期形状推导取值位置（**零迁移**：旧配置不需要新增任何键）。

    规则与引擎逐条对应：选择器非空 ⇒ ``child``；选择器空且有 ``attr`` ⇒ ``element_attr``；
    选择器空且无 ``attr`` ⇒ ``element``。
    """
    selector = str(rule.get(SELECTOR_KEY) or "").strip()
    attr = str(rule.get(ATTR_KEY) or "").strip()
    if selector:
        return POSITION_BY_KEY[POSITION_CHILD]
    return POSITION_BY_KEY[POSITION_ELEMENT_ATTR if attr else POSITION_ELEMENT]


def to_rule(position: str, *, selector: str = "", attr: str = "") -> dict[str, Any]:
    """按位置生成**运行期形状**（GUI 保存时用；config 里不出现 position 键）。

    位置与给定值矛盾时以位置为准（例如 ``element`` 会丢掉 ``attr``）——
    这样"选了位置却还留着旧属性"不会悄悄变成另一种位置。
    """
    shape = POSITION_BY_KEY.get(str(position))
    if shape is None:
        raise ValueError(f"未知的取值位置：{position!r}（可选：{', '.join(POSITION_BY_KEY)}）")
    rule: dict[str, Any] = {}
    if shape.needs_selector:
        rule[SELECTOR_KEY] = str(selector or "").strip()
    else:
        rule[SELECTOR_KEY] = ""
    # 属性名：`element_attr` 必填（缺省给最常见的 href）；`child` 可携带可选属性
    # （"选子链接取 href"就属它）；`element` **刻意丢掉** attr —— 留着它会静默变成
    # `element_attr`（另一种位置），而用户选的是"取自身文本"。
    attr_text = str(attr or "").strip()
    if shape.needs_attribute:
        rule[ATTR_KEY] = attr_text or "href"
    elif attr_text and shape.needs_selector:
        rule[ATTR_KEY] = attr_text
    return rule


def normalize_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    """把一条字段规则收敛成运行期形状：``attribute`` → ``attr``、``selector`` 兜底为 ``""``。

    其余键（``regex`` / ``transforms`` / ``default`` / ``all`` / ``join`` / ``group``）
    **原样保留**——它们同属引擎契约；本函数只统一"取值位置"这两个键的命名。
    """
    normalized: dict[str, Any] = dict(rule)
    alias_value = normalized.pop(ATTRIBUTE_ALIAS, None)
    if ATTR_KEY not in normalized and alias_value not in (None, ""):
        normalized[ATTR_KEY] = alias_value
    normalized.setdefault(SELECTOR_KEY, "")
    return normalized


def validate_value_source(rule: Any, *, label: str = "字段") -> list[str]:
    """校验一条字段的取值位置，返回错误列表（空列表表示通过）。

    只管**结构性**问题（键的存在与类型、以及"选了位置却给不出必需键"）：
    文案与 ``gui/core/config_model.py`` 既有口径保持一致，便于两侧共用同一套结论。
    """
    if not isinstance(rule, Mapping):
        return [f"{label}规则必须是YAML对象"]
    errors: list[str] = []
    if SELECTOR_KEY not in rule:
        errors.append(f"{label}缺少 '{SELECTOR_KEY}'（取元素自身时写空串）")
    else:
        selector = rule[SELECTOR_KEY]
        if selector is None or not isinstance(selector, str):
            errors.append(f"{label}的 '{SELECTOR_KEY}' 必须是字符串")
    if ATTRIBUTE_ALIAS in rule and ATTR_KEY not in rule:
        errors.append(f"{label}使用了旧键名 '{ATTRIBUTE_ALIAS}'，请改为 '{ATTR_KEY}'（由契约归一）")
    attr = rule.get(ATTR_KEY)
    if attr is not None and not isinstance(attr, str):
        errors.append(f"{label}的 '{ATTR_KEY}' 必须是字符串")
    # 反向：取元素自身的属性，却把属性名写成了选择器 —— 这是最容易犯的错，
    # 引擎会照字面执行（把 "<a href=...>" 当 CSS 选择器去找），结果**静默取不到值**。
    selector = str(rule.get(SELECTOR_KEY) or "").strip()
    attr = str(attr or "").strip()
    if selector and not attr and _looks_like_attribute_name(selector):
        errors.append(
            f"{label}的选择器 {selector!r} 看起来是属性名；"
            f"要取条目元素自身的属性，请把选择器留空并把属性填到 '{ATTR_KEY}'"
        )
    return errors


def _looks_like_attribute_name(value: str) -> bool:
    """启发式：纯字母（可含 ``-``/``_``）且是常见值载体属性 ⇒ 更像属性名而不是 CSS 选择器。"""
    return value in COMMON_ATTRIBUTES
