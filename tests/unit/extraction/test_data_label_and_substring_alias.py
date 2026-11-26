"""`data-label` / 子串别名 —— 两条零依赖改动（2026-11-26）。

## 为什么只做这两条

用户问「扩语料 / Gap 检索 / 向量化 能不能提高字段对应」。实测后：

| 方案 | 实测 |
|---|---|
| 现状（别名 + difflib） | held-out 41% |
| 扩同义词表 | held-out **73%**（我自编用例的自测 96% 是自证） |
| + 子串别名 | held-out **83%** |
| 向量化 | 需 135MB~3GB；而剩余失败里**几乎没有**它能救的 |

## ① `data-label` 是数据值的语义标注

实测 randomuser.me：`<li data-label="name" data-value="...">` ——
站点把「这一列是什么」写得比 class 清楚得多，旧实现完全没读这个属性。

★ 但刻意**不**认 `aria-label`：实测它主要标在交互控件上
（`<a aria-label="Navigation category">`、`<button aria-label="Close">`），
当字段用会把导航整片算成业务列 —— 我第一版认了它，webscraper 夹具当场打红。

## ② 子串别名（不做相似度）

「邮箱地址」只是「邮箱」加了个修饰词。逐字查表永远差一个字。
所以在**别名表内部**做一次包含匹配（双向）。
★ 只做**包含**，不做相似度：「卡片标题 → 正文」这种跨语义的必须留给用户。

## 诚实性说明

我第一轮给出的「扩表 96%」是**自证**：用例和词表都是我编的。
改成 held-out（词表只收常见说法、另 41 条更冷门的当检验集）后：

    41% → 73%（扩表）→ 83%（+ 子串）

这中间的差距就是「真实收益」和「我编出来的收益」的差。
"""

from __future__ import annotations

import pytest

from omnicrawler.extraction.intelligent_scraper import align_requested_fields
from omnicrawler.extraction.intelligent_scraper.dom_features import _parse_dom

# ── ① data-label ─────────────────────────────────────────────────────────


def test_data_label_is_parsed_and_used_as_field_identity() -> None:
    """`data-label` 是站点显式写下的「这个值是什么」。"""
    html = "<html><body><ul>" + "".join(
        f'<li data-label="{label}" data-value="{i}">x{i}</li>'
        for i, label in enumerate(["name", "email", "phone"], 1)
    ) + "</ul></body></html>"
    nodes = [n for n in _parse_dom(html) if n.tag == "li"]
    labels = [n.label for n in nodes]
    assert labels == ["name", "email", "phone"], f"data-label 没读到：{labels}"
    # ★ 光断言「读到了」不够 —— 真正要守的是「**当成字段身份用了**」。
    #   我第一版只断言 labels，反向断言当场判红：把 `if node.label:` 删掉，
    #   `n.label` 照样有值（解析层还在填），测试看不出差别。
    from omnicrawler.extraction.intelligent_scraper.field_inference import _field_key

    assert _field_key(nodes[0]).startswith("label:"), (
        f"data-label 没被当成字段身份：{_field_key(nodes[0])}"
    )


def test_data_value_is_preferred_over_element_text() -> None:
    """* data-label 常与 data-value 成对出现，值是属性不是文本。

    实测 randomuser.me：li 上是 data-label="name" data-value="Bret"，元素文本为空。
    旧实现只写 text，于是取到空串 —— 这一列「存在但永远没有值」，比整列缺席更隐蔽。

    这里只断言解析层。不连字段规则一起断言，是因为 analyze_to_config 要求先识别
    出列表容器，而这种「一个 card 里若干 li」的页面本来就是单对象形态；
    硬造夹具会变成在测「我编的页面结构」而不是产品行为。
    """
    html = (
        "<html><body><ul>"
        + "".join(
            f'<li data-label="{k}" data-value="{v}"></li>'
            for k, v in [("name", "Bret"), ("email", "b@e.com")]
        )
        + "</ul></body></html>"
    )
    nodes = [n for n in _parse_dom(html) if n.tag == "li"]
    assert [n.data_value for n in nodes] == ["Bret", "b@e.com"]
    assert all(n.label in {"name", "email"} for n in nodes)


def test_aria_label_is_read_but_not_used_as_field_identity() -> None:
    """★ `aria-label` 会被读到，但**不进字段身份**。

    实测 webscraper.io：导航里一堆 `<a aria-label="Navigation category">`，
    认它会把整片导航算成业务列 —— 我第一版就这么错，夹具当场打红。
    """
    html = (
        '<html><body><nav>'
        '<a href="/1" aria-label="Navigation category">Computers</a>'
        '<a href="/2" aria-label="Navigation category">Phones</a>'
        "</nav></body></html>"
    )
    nodes = [n for n in _parse_dom(html) if n.tag == "a"]
    assert nodes and all(n.aria_label == "Navigation category" for n in nodes), (
        "aria-label 应当被**读到**（record-actions 等路径要用）"
    )
    # 行为断言：只有 aria-label 的节点，`_field_key` **不许**给它一个 aria 身份。
    #   ★ 我第一版写成源码断言（`"aria" not in source`），当场判红 ——
    #     因为**注释里就写着「刻意不认 aria」**。
    #     源码断言在这里是错的形态：它测的是文本，不是行为。
    from omnicrawler.extraction.intelligent_scraper.field_inference import _field_key

    aria_node = next(n for n in nodes if n.aria_label)
    key = _field_key(aria_node)
    assert not key.startswith("aria:"), f"aria-label 被当成字段身份了：{key}"


# ── ② 子串别名 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("demand", "expected"),
    [
        ("邮箱地址", "邮箱"),
        ("电话号码", "电话"),
        ("标题文字", "标题"),
        ("详细介绍", "描述"),
    ],
)
def test_substring_alias_resolves(demand: str, expected: str) -> None:
    """「基础词 + 修饰」不该让整条命令失败。"""
    pool = {
        n: {"selector": f".{n}"}
        for n in ["邮箱", "电话", "标题", "描述", "价格", "正文"]
    }
    picked = align_requested_fields(dict(pool), [demand], [])
    selector = str(picked[demand].get("selector", ""))
    assert selector == f".{expected}", f"{demand} 匹到了 {selector}"


def test_cross_semantic_demand_still_fails_loudly() -> None:
    """★ 包含匹配不能退化成「差不多就行」。

    「卡片标题」与「正文」字形上毫不相干，必须报错让用户改词 ——
    静默配成「正文」等于替用户猜，而抓错了比不抓更糟。
    """
    with pytest.raises(ValueError, match="卡片标题"):
        align_requested_fields({"正文": {"selector": ".c"}}, ["卡片标题"], [])
