"""多粒度共存的**端到端**验证（存储 → 读取），2026-11-26。

单元测试只覆盖 `storage_identity()` 的返回值。这一层补的是「真的写进 DB 之后
能不能两种都读到」—— 因为 bug 的表现形态是**记录数少一条**，不是报错。
"""

from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.state import StateStore


def test_both_grains_survive_a_real_write(tmp_path) -> None:
    """★ 实测失败的原始场景：30 条列表 + 1 条单品。

    修之前 DB 里只有 29 + 1（第 1 条列表记录被单品挤掉）；
    修之后必须是 30 + 1。

    为什么用 30：这不是随手写的数字，就是我当时复现时用的条数；
    而这个 bug 只在**第一条**相撞，用 2 条也能红，用 30 条是为了让
    「少一条」在断言里显形。
    """
    store = StateStore(tmp_path / "s.db")
    run_id = store.start_run("t", str(tmp_path / "c.yaml"))
    req = CrawlRequest("https://example.test/page")

    listing = [
        ExtractedRecord(req.url, "html_item", {"标题": f"条目{i}"}, {})
        for i in range(30)
    ]
    detail = ExtractedRecord(
        req.url, "product_detail", {"产品名": "整页信息", "定价": "Free"}, {}
    )

    assert store.save_records(run_id, req, listing) == 30
    assert store.save_records(run_id, req, [detail]) == 1

    rows = store.rows(
        "SELECT record_type, COUNT(*) AS c FROM records GROUP BY record_type ORDER BY record_type"
    )
    counts = {row["record_type"]: row["c"] for row in rows}
    assert counts == {"html_item": 30, "product_detail": 1}, (
        f"两种粒度没有都保住：{counts}（修前就是这里少一条）"
    )


def test_repeated_save_of_same_page_does_not_double(tmp_path) -> None:
    """★ 修碰撞不能换来「重跑翻倍」。

    同一页重复保存两次，记录数必须不变 —— 否则修了一个 bug、造了一个更大的。
    """
    store = StateStore(tmp_path / "s.db")
    run_id = store.start_run("t", str(tmp_path / "c.yaml"))
    req = CrawlRequest("https://example.test/page")
    batch = [
        ExtractedRecord(req.url, "html_item", {"标题": f"条目{i}"}, {}) for i in range(5)
    ]

    store.save_records(run_id, req, batch)
    store.save_records(run_id, req, batch)

    total = store.rows("SELECT COUNT(*) AS c FROM records")[0]["c"]
    assert total == 5, f"重复保存后记录数变了：{total}（应为 5）"
