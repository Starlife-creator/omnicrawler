from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.commands.task import compile_request
from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.extraction.intelligent_scraper import align_requested_fields, analyze_to_config
from omnicrawler.state import StateStore

REQUEST = "从 https://quotes.toscrape.com/js/ 采集前两页名言正文、作者和全部标签，导出 Excel。"


def test_original_goal_preserves_chinese_pages_fields_multiple_and_format() -> None:
    task = compile_request(REQUEST)["task"]
    assert task["max_pages"] == 2
    assert task["fields"] == ["正文", "作者", "标签"]
    assert task["multi_value_fields"] == ["标签"]
    assert task["output_formats"] == ["xlsx"]


@pytest.mark.parametrize("phrase,pages", [("仅一页", 1), ("前十页", 10), ("前十二页", 12), ("共二十页", 20)])
def test_chinese_page_goal_boundaries(phrase: str, pages: int) -> None:
    assert compile_request(f"从 https://example.org/ 采集{phrase}标题和价格")["task"]["max_pages"] == pages


def test_missing_goal_fields_are_rejected_before_success() -> None:
    with pytest.raises(ValueError, match="秘密字段"):
        align_requested_fields({"作者": {"selector": ".author"}}, ["秘密字段", "作者"], [])


def test_product_title_uses_matching_full_attribute() -> None:
    items = "".join(f'<article class="product_pod"><h3><a href="/product/{i}?edition=1" title="Book {i} complete title">Book {i}...</a></h3><p class="price_color">£{i}.00</p></article>' for i in range(1,4))
    config = analyze_to_config(f"<html><body><section>{items}</section></body></html>", "https://example.org/")
    assert config["extract"]["fields"]["标题"].get("attr") == "title"
    assert config["extract"]["fields"]["链接地址"]["attr"] == "href"
    assert config["extract"]["deduplicate_by"] == ["链接地址"]


def test_product_detail_is_one_record_not_property_rows() -> None:
    html = '<html><body><div class="product_main"><h1>A complete book</h1><p class="price_color">£12.00</p></div><table>' + ''.join(f'<tr><th>Property {i}</th><td>{i}</td></tr>' for i in range(7)) + '</table></body></html>'
    config = analyze_to_config(html, "https://example.org/product/1")
    assert set(config["extract"]["fields"]) == {"名称", "价格"}
    assert "product_main" in config["extract"]["item_selector"]


def test_stable_entity_duplicate_delivery_and_conservative_fallback(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        run = state.start_run("entity", "config.yaml")
        request = CrawlRequest("https://example.org/")
        alias = CrawlRequest("https://example.org/index.html")
        row = ExtractedRecord(request.url, "item", {"标题": "Same", "链接地址": "https://example.org/product?id=1", "价格": "10"})
        assert state.save_records(run, request, [row], deduplicate_by=("链接地址",)) == 1
        assert state.save_records(run, alias, [row], deduplicate_by=("链接地址",)) == 0
        # Same title with a different stable query identity remains a different product.
        other = ExtractedRecord(alias.url, "item", {**row.data, "链接地址": "https://example.org/product?id=2"})
        assert state.save_records(run, alias, [other], deduplicate_by=("链接地址",)) == 1
        # Changed data is retained, including a change in letter case.
        changed = ExtractedRecord(alias.url, "item", {**row.data, "标题": "same"})
        assert state.save_records(run, alias, [changed], deduplicate_by=("链接地址",)) == 1
        missing = ExtractedRecord(alias.url, "item", {"标题": "Same"})
        assert state.save_records(run, request, [missing], deduplicate_by=("链接地址",)) == 1
        assert state.save_records(run, alias, [missing], deduplicate_by=("链接地址",)) == 1


def test_tags_inference_collects_all_values() -> None:
    items = "".join(f'<div class="quote"><span class="text">Quotation {i} sufficiently long</span><small class="author">Author {i}</small><div class="tags"><a class="tag" href="/tag/a">alpha</a><a class="tag" href="/tag/b">beta</a></div></div>' for i in range(3))
    config = analyze_to_config(f"<html><body>{items}</body></html>", "https://example.org/")
    assert config["extract"]["fields"]["标签"].get("all", False)
