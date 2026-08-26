"""task_canvas_logic 纯逻辑单测（FINAL 长期债 #1 Phase C）。"""

from __future__ import annotations

from omnicrawler.gui.core.config_model import FieldDef
from omnicrawler.gui.views.task_canvas_logic import field_fingerprint, selector_kind


def _field(name: str = "title", selector: str = "h1", kind: str = "css") -> FieldDef:
    return FieldDef(name=name, selector=selector, selector_type=kind)


def test_fingerprint_stable_and_order_sensitive() -> None:
    a = field_fingerprint([_field("t"), _field("p")])
    a2 = field_fingerprint([_field("t"), _field("p")])
    b = field_fingerprint([_field("p"), _field("t")])
    assert a == a2, "同序同字段必须同指纹"
    assert a != b, "有序序列化：顺序变化必须改变指纹"


def test_fingerprint_ignores_non_core_attrs() -> None:
    """仅 name/selector/selector_type 参与序列化——展示性属性变更不失效。"""
    base = _field("t", "h1", "css")
    variant = _field("t", "h1", "css")
    if hasattr(variant, "required"):
        variant.required = not base.required
    assert field_fingerprint([base]) == field_fingerprint([variant])


def test_fingerprint_detects_selector_change() -> None:
    assert field_fingerprint([_field("t", "h1")]) != field_fingerprint([_field("t", "main h1")])


def test_selector_kind_classification() -> None:
    assert selector_kind("") == "css"
    assert selector_kind("h1.title") == "css"
    assert selector_kind("//div[@id='x']") == "xpath"
    assert selector_kind(".//span") == "xpath"
    assert selector_kind("(//a)[1]") == "xpath"
    assert selector_kind("a[@href]") == "xpath"  # [@ 谓词形式
