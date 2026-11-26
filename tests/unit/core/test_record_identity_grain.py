"""两种记录粒度共存时不许互相覆盖（2026-11-26）。

## 实测出来的 bug

同一次处理里产出「30 条列表项 + 1 条整页单品信息」时，DB 里只剩 **29 + 1** ——
**第 1 条列表记录静默消失**，没有任何告警、没有任何异常。

根因是 `storage_identity()` 的两条分支不一致：

```python
# stable 分支（有 deduplicate_by 时）—— 带了 record_type
key = f"{run_id}:entity:{record.record_type}:{...}"
# 非 stable 分支（默认）—— **漏了** record_type
key = f"{run_id}:{request.fingerprint}:{index}"
```

于是同一 URL 的 `html_item[1]` 与 `product_detail[1]` 算出**同一个 record_id**，
`INSERT OR IGNORE` / `INSERT OR REPLACE` 让后写的挤掉先写的。

## 为什么这条特别值得钉

它不是「某站抓不到」，而是**抓到了却丢了**。用户看到的记录数少一条，
既不会报错也不会告警，只会以为那页就 29 条。
"""

from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.core.record_storage_identity import storage_identity

_REQ = CrawlRequest("https://example.test/page")


def test_different_record_types_never_collide_at_same_index() -> None:
    """★ 核心回归：同 URL、同 index、不同 record_type ⇒ 必须是两个 id。

    实测形态：一页既产出列表项（html_item）又产出整页单品信息
    （product_detail），两者的 index 都从 1 开始。
    """
    list_item = ExtractedRecord(_REQ.url, "html_item", {"标题": "A"}, {})
    detail = ExtractedRecord(_REQ.url, "product_detail", {"产品名": "B"}, {})

    list_id, _ = storage_identity("run", _REQ, list_item, 1)
    detail_id, _ = storage_identity("run", _REQ, detail, 1)

    assert list_id != detail_id, (
        "两种记录类型在同 index 上撞了 —— 后写的会把先写的挤掉（实测 30 条变 29 条）"
    )


def test_same_type_same_index_still_deduplicates() -> None:
    """★ 修碰撞不能顺手把**同一件事**的去重也弄坏。

    同 URL、同 index、同 record_type 重复保存，必须仍然是同一个 id ——
    否则重跑同一个页面会翻倍出记录。
    """
    a = ExtractedRecord(_REQ.url, "html_item", {"标题": "A"}, {})
    b = ExtractedRecord(_REQ.url, "html_item", {"标题": "A"}, {})

    assert storage_identity("run", _REQ, a, 1)[0] == storage_identity("run", _REQ, b, 1)[0]


def test_stable_branch_and_default_branch_use_the_same_ingredients() -> None:
    """两条分支都必须含 record_type —— 这是 bug 的真正根因。

    不做源码断言（那种断言会被注释干扰，本会话已经踩过），
    而是构造同一个 record 的 stable / 非 stable 两个调用，看是否只差在去重键上。
    """
    rec = ExtractedRecord(_REQ.url, "product_detail", {"id": "x1", "产品名": "B"}, {})

    plain_id, stable_plain = storage_identity("run", _REQ, rec, 1)
    keyed_id, stable_keyed = storage_identity("run", _REQ, rec, 1, ("id",))

    assert not stable_plain and stable_keyed
    assert plain_id != keyed_id, "两支的分键方式本就该不同（一支含实体去重键）"

    # 但「换一个 record_type」在**两支里**都必须换 id
    other = ExtractedRecord(_REQ.url, "html_item", {"id": "x1", "产品名": "B"}, {})
    assert storage_identity("run", _REQ, other, 1)[0] != plain_id
    assert storage_identity("run", _REQ, other, 1, ("id",))[0] != keyed_id
