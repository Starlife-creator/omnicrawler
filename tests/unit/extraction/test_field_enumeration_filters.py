"""走查 R3.5：候选字段枚举必须放行「取值恒定但有语义类名」的真实业务列。

背景（0.13.0 实测，2026-09-18 走查 R3.5）：`books.toscrape.com` 上
`<p class="instock availability">In stock</p>` 在样本内**取值恒定**，
而 `_infer_item_fields` 的「恒定文案 ⇒ 模板噪声」判据把它**整列删掉** ——
于是「库存状态」根本没进入候选（不是命名错，是没枚举到）。

本文件用**最小 DOM** 直接驱动 `_infer_item_fields`（而不是只测那个谓词），
确保这条过滤真的被守卫住。
"""

from __future__ import annotations

from omnicrawler.extraction.intelligent_scraper import DOMNode, _infer_item_fields

_CONTAINER = "body > ul"


def _siblings(*, count: int, tag: str, classes: list[str], texts: list[str], depth: int = 3) -> list[DOMNode]:
    return [
        DOMNode(
            tag=tag,
            classes=list(classes),
            text=texts[index],
            depth=depth,
            css_path=f"{_CONTAINER} > li.item > {tag}.{'.'.join(classes)}" if classes else f"{_CONTAINER} > li.item > {tag}",
        )
        for index in range(count)
    ]


def _dom() -> tuple[list[DOMNode], list[DOMNode]]:
    count = 6
    items = [
        DOMNode(tag="li", classes=["item"], depth=2, css_path=f"{_CONTAINER} > li.item", children_tags=["span", "i"])
        for _ in range(count)
    ]
    nodes = [
        *items,
        *_siblings(count=count, tag="span", classes=["title"], texts=[f"T{i}" for i in range(count)]),
        *_siblings(count=count, tag="span", classes=["price"], texts=[f"${i}.00" for i in range(count)]),
        # ★ 真实业务列，但样本内取值恒定
        *_siblings(count=count, tag="span", classes=["stock"], texts=["In stock"] * count),
        # 模板噪声：同样恒定、但没有语义类名
        *_siblings(count=count, tag="i", classes=["icon"], texts=["(about)"] * count),
    ]
    return items, nodes


def test_constant_column_with_semantic_class_is_kept() -> None:
    items, nodes = _dom()
    fields = _infer_item_fields(_CONTAINER, items, nodes)
    names = {str(field.get("field_name") or field.get("name")) for field in fields}
    assert "库存状态" in names, f"取值恒定但有语义类名的列不得被当成模板噪声删掉：{names}"


def test_constant_column_without_semantic_class_is_still_dropped() -> None:
    items, nodes = _dom()
    fields = _infer_item_fields(_CONTAINER, items, nodes)
    selectors = " ".join(str(field.get("selector", "")) for field in fields)
    assert "i.icon" not in selectors, "无语义类名的恒定文案仍应作为模板噪声被过滤"


def test_varying_columns_are_unaffected() -> None:
    items, nodes = _dom()
    fields = _infer_item_fields(_CONTAINER, items, nodes)
    names = {str(field.get("field_name") or field.get("name")) for field in fields}
    assert {"标题", "价格"} <= names, names


def test_price_and_stock_are_not_confused_under_a_shared_ancestor() -> None:
    """★ 祖先类名兜底不得压过自身信号（走查 R3.4 的过度套用回归）。"""
    items = [
        DOMNode(
            tag="div", classes=["product_price"], depth=2,
            css_path=f"{_CONTAINER} > li.item > div.product_price", children_tags=["p"],
        )
        for _ in range(6)
    ]
    nodes = [
        *items,
        *[
            DOMNode(
                tag="p", classes=["price_color"], text=f"£{index}.00", depth=4,
                css_path=f"{_CONTAINER} > li.item > div.product_price > p.price_color",
            )
            for index in range(6)
        ],
        *[
            DOMNode(
                tag="p", classes=["instock", "availability"], text="In stock", depth=4,
                css_path=f"{_CONTAINER} > li.item > div.product_price > p.instock.availability",
            )
            for _ in range(6)
        ],
    ]
    fields = _infer_item_fields(_CONTAINER, items, nodes)
    by_name = {
        str(field.get("field_name") or field.get("name")): str(field.get("selector", ""))
        for field in fields
    }
    price_selector = next((sel for name, sel in by_name.items() if name.startswith("价格")), "")
    assert "price_color" in price_selector, by_name
    stock_selector = next((sel for name, sel in by_name.items() if "库存" in name), "")
    assert "instock" in stock_selector, by_name
