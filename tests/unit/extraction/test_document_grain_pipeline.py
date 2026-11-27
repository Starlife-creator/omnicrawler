"""文档级字段拆分 + 导出告警的端到端守卫（2026-11-27）。

单测只覆盖「判断某条规则是不是文档级」。这一层补的是：

1. 真的跑 `HTMLProcessor` 时，文档级字段**没有**进列表行；
2. 它单独成一条 `record_type=document` 的记录；
3. 导出时给出**可读告警**（而不是让用户对着一张空列过半的表发愣）。
"""

from __future__ import annotations

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.extraction.extractors import HTMLProcessor
from omnicrawler.pipeline.exporters import _export_delivery
from omnicrawler.state import StateStore

_HTML = """<html><head>
<meta property="og:description" content="整页描述文字">
<script type="application/ld+json">{"@type":"Product","name":"整页产品名"}</script>
</head><body>
<div class="item"><h2 class="title">条目一</h2></div>
<div class="item"><h2 class="title">条目二</h2></div>
<div class="item"><h2 class="title">条目三</h2></div>
</body></html>"""


def _config(tmp_path, fields, name="t"):
    workspace = tmp_path / "work" / name
    workspace.mkdir(parents=True, exist_ok=True)
    raw = {
        "project": {"name": name, "workspace": str(workspace)},
        "source": {"kind": "static_html", "seeds": ["https://example.test/p"]},
        "crawl": {"max_pages": 1},
        "http": {"user_agent": "test", "respect_robots": True},
        "browser": {"engine": "playwright", "headless": True},
        "extract": {"mode": "html", "item_selector": "div.item", "fields": fields},
        "outputs": {"jsonl": False, "csv": False, "xlsx": False},
    }
    return AppConfig(tmp_path / f"{name}.yaml", tmp_path, raw, workspace)


def _result(html: str) -> FetchResult:
    req = CrawlRequest("https://example.test/p")
    return FetchResult(req, req.url, 200, {}, html.encode(), 0.0)


def _export_raw(workspace) -> dict:
    return {
        "project": {"name": workspace.name, "workspace": str(workspace)},
        "source": {"kind": "static_html", "seeds": ["https://example.test/p"]},
        "crawl": {"max_pages": 1},
        "http": {"user_agent": "test", "respect_robots": True},
        "browser": {"engine": "playwright", "headless": True},
        "extract": {"mode": "html", "fields": {"标题": {"selector": "h1"}}},
        "outputs": {"jsonl": True, "csv": True, "xlsx": False},
    }


def test_document_field_is_not_broadcast_into_listing_rows(tmp_path) -> None:
    """★ 核心回归：文档级字段绝不能出现在列表行里。

    修之前实测：30 行都拿到同一个 "Free"，看起来像抓到了、其实是假数据。
    """
    cfg = _config(tmp_path, {
        "标题": {"selector": ".title"},
        "产品名": {"source": "jsonld", "path": "name"},
    })
    records = HTMLProcessor(cfg).process(_result(_HTML)).records

    listing = [r for r in records if r.record_type == "html_item"]
    assert len(listing) == 3, f"列表项数不对：{len(listing)}"
    for row in listing:
        assert "产品名" not in row.data, f"文档级字段被广播进列表行了：{row.data}"
        assert row.data.get("标题"), "列表字段丢了"


def test_document_field_becomes_its_own_record(tmp_path) -> None:
    """文档级值必须**恰好出现一次**，且能取到真值。"""
    cfg = _config(tmp_path, {
        "标题": {"selector": ".title"},
        "产品名": {"source": "jsonld", "path": "name"},
        "整页描述": {"source": "opengraph", "property": "description"},
    })
    records = HTMLProcessor(cfg).process(_result(_HTML)).records

    docs = [r for r in records if r.record_type == "document"]
    assert len(docs) == 1, f"文档级记录应当恰好 1 条，实际 {len(docs)}"
    assert docs[0].data.get("产品名") == "整页产品名"
    assert docs[0].data.get("整页描述") == "整页描述文字"


def test_no_list_container_keeps_a_single_record(tmp_path) -> None:
    """★ 单页单对象时**不许**拆。

    没有 item_selector 时「一行」就等于「整页」，拆开只会平白多一条重复记录。
    """
    cfg = _config(tmp_path, {
        "标题": {"selector": ".title"},
        "产品名": {"source": "jsonld", "path": "name"},
    })
    cfg.section("extract")["item_selector"] = ""
    records = HTMLProcessor(cfg).process(_result(_HTML)).records

    assert len(records) == 1, f"单对象页被拆成了 {len(records)} 条"
    assert records[0].data.get("产品名") == "整页产品名"


def test_export_warns_when_multiple_grains_are_present(tmp_path) -> None:
    """★ 两种粒度混在一张表里必须**说出来**。

    实测形态：29 行在「定价」列上全空。不报错、不丢数据，
    但用户只会以为「定价没抓到」。
    """
    workspace = tmp_path / "work" / "w"
    workspace.mkdir(parents=True)
    store = StateStore(tmp_path / "s.db")
    run_id = store.start_run("t", str(tmp_path / "c.yaml"))
    req = CrawlRequest("https://example.test/p")

    store.save_records(run_id, req, [
        ExtractedRecord(req.url, "html_item", {"标题": f"条目{i}"}, {}) for i in range(3)
    ])
    store.save_records(run_id, req, [
        ExtractedRecord(req.url, "document", {"产品定价": "Free"}, {})
    ])

    out = _export_delivery(
        AppConfig(tmp_path / "w.yaml", tmp_path, _export_raw(workspace), workspace),
        store,
        run_id,
    )

    assert out.get("records") == 4, "记录数不对：{}".format(out.get("records"))
    joined = " ".join(out.get("warnings") or [])
    assert "record_type" in joined, f"没有告知如何分流：{joined[:200]}"
    assert "html_item" in joined and "document" in joined, (
        f"告警里没有列出两种粒度：{joined[:200]}"
    )


def test_export_does_not_warn_for_a_single_grain(tmp_path) -> None:
    """★ 单一粒度时不许叨叨 —— 那会让正常结果看起来有问题。"""
    workspace = tmp_path / "work" / "s"
    workspace.mkdir(parents=True)
    store = StateStore(tmp_path / "s.db")
    run_id = store.start_run("t", str(tmp_path / "c.yaml"))
    req = CrawlRequest("https://example.test/p")
    store.save_records(run_id, req, [
        ExtractedRecord(req.url, "html_item", {"标题": f"条目{i}"}, {}) for i in range(3)
    ])

    out = _export_delivery(
        AppConfig(tmp_path / "s.yaml", tmp_path, _export_raw(workspace), workspace),
        store,
        run_id,
    )
    joined = " ".join(out.get("warnings") or [])
    assert "多种记录粒度" not in joined, f"单一粒度不该告警：{joined[:200]}"
