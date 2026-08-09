from __future__ import annotations

import pytest
import yaml

from omnicrawl.core.config import load_config
from omnicrawl.extraction.intelligent_scraper import analyze_to_config

LIST_PAGE = """<html><body>
<div class="items">
  <div class="item"><h2><a href="/p1">Apple iPhone</a></h2><span class="price">699</span></div>
  <div class="item"><h2><a href="/p2">Samsung Galaxy</a></h2><span class="price">799</span></div>
  <div class="item"><h2><a href="/p3">Xiaomi Mi</a></h2><span class="price">499</span></div>
  <div class="item"><h2><a href="/p4">Pixel Phone</a></h2><span class="price">599</span></div>
</div>
<a href="/list?page=2">下一页</a>
</body></html>"""


def test_analyze_to_config_placeholder_url_rejected() -> None:
    with pytest.raises(ValueError, match="真实页面 URL"):
        analyze_to_config("<html><body><p>x</p></body></html>", url="")
    with pytest.raises(ValueError, match="真实页面 URL"):
        analyze_to_config("<html><body><p>x</p></body></html>", url="file:///placeholder")


def test_analyze_to_config_contract_keys(tmp_path) -> None:
    config = analyze_to_config(
        LIST_PAGE, url="https://shop.example/list?page=1", project_name="auto-demo"
    )

    assert config["source"]["seeds"] == ["https://shop.example/list?page=1"]
    assert config["source"]["pagination"] == {"type": "page", "parameter": "page"}
    assert "pagination" not in config["crawl"]

    fields = config["extract"]["fields"]
    assert fields, "应推断出业务字段"
    for _name, rule in fields.items():
        assert "selector" in rule
        assert "attribute" not in rule, "契约用 attr，不使用 attribute"
        assert "desc" not in rule, "契约字段不含 desc"
        for key in rule:
            assert key in ("selector", "attr", "regex", "examples"), f"未知字段键: {key}"

    assert config["extract"].get("item_selector"), "列表容器应输出到 item_selector"


def test_analyze_to_config_passes_core_validation(tmp_path) -> None:
    config = analyze_to_config(LIST_PAGE, url="https://shop.example/list?page=1")
    path = tmp_path / "auto.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    loaded = load_config(path)  # 契约校验通过，不抛 ValueError
    assert loaded.source_kind == "browser"
    assert loaded.section("source").get("seeds")
