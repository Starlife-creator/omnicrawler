"""单页模式字段推断：短值不得被静默丢掉（N1c 剩余项，2026-09-14 修正）。

## 修的是什么（实测驱动）

旧实现只有一条 `len(text) < 5` 判据，同时想干两件事（挡 UI 装饰 + 挡噪声），结果**误伤数据**。
实测一个普通商品详情页（`_DETAIL_PAGE`）：

| 节点 | 旧行为 | 现在 |
|---|---|---|
| `<h1>示例商品</h1>`（4 字**标题**） | 被丢 ⇒ **标题字段整个没有** | 保留为「标题」 |
| `<span>9</span>` / `<span>元</span>` | 被丢，父 `div.price` 的合并文本 `'9\\n    元'` 当了"价格"（**脏值**） | 叶子精确选择器，值分别是 `9` / `元` |
| 同名但选择器不同的字段 | **按名字去重** ⇒ 只留第一个 | 都保留（去重键含 CSS 路径） |
| 导航 / 页脚链接 | 被丢（对） | 仍被丢（改由**区域**判据挡） |

判据：**长度不是「是不是数据」的判据**——`9` / `元` / `A4` 短，但它们正是要采的值；
「是不是 UI 装饰」交给区域（`_is_chrome_path`：aside / nav / footer）。
"""

from __future__ import annotations

from omnicrawler.extraction.intelligent_scraper import (
    _MAX_SINGLE_PAGE_FIELDS,
    _infer_single_page_fields,
    _parse_dom,
    infer_fields,
)

_DETAIL_PAGE = """
<html><body>
  <nav><a href="/">首页</a><a href="/list">全部商品</a></nav>
  <h1>示例商品</h1>
  <div class="price">
    <span class="amount">9</span>
    <span class="unit">元</span>
  </div>
  <ul class="specs"><li>颜色：红色</li><li>尺码：A4</li></ul>
  <p class="desc">这是一段较长的商品描述文字，用来保证至少有一个字段长度过关。</p>
  <footer><a href="/about">关于我们</a></footer>
</body></html>
"""


def _fields(html: str) -> list[dict]:
    return _infer_single_page_fields(_parse_dom(html), "https://example.org/item/1")


def _names(fields: list[dict]) -> list[str]:
    return [field["name"].rsplit("_", 1)[0] for field in fields]


def test_short_values_are_not_dropped() -> None:
    """4 字标题、1 位价格、1 字单位都必须出现——这正是旧实现丢掉的。"""
    fields = _fields(_DETAIL_PAGE)
    names = _names(fields)
    assert "标题" in names, f"标题（4 字）被丢了：{fields}"
    values = [field["examples"][0] for field in fields]
    assert "示例商品" in values, values
    examples = " ".join(values)
    assert "9" in examples and "元" in examples, f"短值 9 / 元 被丢了：{values}"


def test_container_text_is_not_used_as_a_dirty_value() -> None:
    """价格取自叶子 `span.amount`，不是父节点的合并文本 `'9\\n    元'`。"""
    fields = _fields(_DETAIL_PAGE)
    by_value = {field["examples"][0]: field for field in fields}
    assert "9" in by_value, f"应有取值 9 的字段：{by_value}"
    assert by_value["9"]["selector"].endswith("span.amount"), by_value["9"]
    for field in fields:
        assert "\n" not in field["examples"][0], f"不该出现带换行的合并脏值：{field}"
        assert "\n" not in field["desc"], field


def test_navigation_and_footer_links_are_still_skipped() -> None:
    """区域判据仍要挡住装饰：导航 / 页脚里的链接不得成为字段。"""
    fields = _fields(_DETAIL_PAGE)
    values = [field["examples"][0] for field in fields]
    selectors = [field["selector"] for field in fields]
    for chrome in ("首页", "全部商品", "关于我们"):
        assert chrome not in values, f"页面框架内容不应成为字段：{chrome}"
    assert not any("nav" in selector or "footer" in selector for selector in selectors), selectors


def test_same_name_with_different_selector_is_kept() -> None:
    """旧实现按**名字**去重 ⇒ 同名但选择器不同的字段被挤掉；现在都要保留。"""
    html = """
    <html><body>
      <div class="main"><h1>主标题</h1></div>
      <div class="aside-block"><h1>副标题</h1></div>
    </body></html>
    """
    fields = _fields(html)
    selectors = [field["selector"] for field in fields]
    assert len(selectors) == 2, f"同名但不同选择器应各出一个字段：{fields}"
    assert selectors[0] != selectors[1], selectors


def test_pure_punctuation_is_not_a_field() -> None:
    """只含标点/分隔符的节点不是数据（长度判据换掉后，这条由可取值字符判据承担）。"""
    html = "<html><body><div><span>·</span><span>&gt;&gt;</span><p>有效内容</p></div></body></html>"
    fields = _fields(html)
    values = [field["examples"][0] for field in fields]
    assert values == ["有效内容"], values


def test_field_cap_is_documented_and_enforced() -> None:
    """上限存在（挡"页面很长 ⇒ 候选爆量"），但它不该被当作丢数据的判据。"""
    spans = "".join(
        f'<span class="f{index}">值{index}</span>'
        for index in range(_MAX_SINGLE_PAGE_FIELDS + 10)
    )
    fields = _fields(f"<html><body><div class=\"box\">{spans}</div></body></html>")
    assert len(fields) == _MAX_SINGLE_PAGE_FIELDS, len(fields)


def test_infer_fields_without_patterns_uses_the_same_rules() -> None:
    """没有重复模式时（详情页）走同一条路径，行为一致。"""
    nodes = _parse_dom(_DETAIL_PAGE)
    fields = infer_fields([], nodes, "https://example.org/item/1")
    assert "标题" in _names(fields), fields
    assert [field["examples"][0] for field in fields if field["examples"][0] == "9"], fields
