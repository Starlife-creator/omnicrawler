"""TaskCanvas 纯逻辑缝（FINAL 长期债 #1 Phase C——Strangler 第一刀）。

从 task_canvas.py 抽出的与 Qt 无关的纯函数，作为后续渐进置换的接缝：
- field_fingerprint：试跑一致性校验的字段集指纹（PRD §2.2.3）
- selector_kind：XPath/CSS 判定（与 step3_fields.selector_kind 同语义）

UI 结构性拆分（五区子控件）待视觉回归基线建立后再行推进。
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Literal

from ..core.config_model import FieldDef


def field_fingerprint(fields: Iterable[FieldDef]) -> str:
    """字段指纹：字段名 + 选择器 + 类型的有序序列化 MD5。

    刻意只序列化 name/selector/selector_type 三元组——顺序无关的展示性
    属性（required/help 等）变更不应使试跑指纹失效。
    """
    parts = [
        f"{f.name}\x1f{f.selector}\x1f{f.selector_type}" for f in fields
    ]
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()


def selector_kind(selector: str) -> Literal["css", "xpath"]:
    """判断选择器是 XPath 还是 CSS（默认 css）；与 step3_fields.selector_kind 同语义。"""
    stripped = (selector or "").strip()
    if not stripped:
        return "css"
    # XPath 通常以 / .// ( @ [ 或 // 开头；CSS 选择器不会
    if stripped.startswith(("/", ".//", "(", "@", "//")) or "[@" in stripped:
        return "xpath"
    return "css"
