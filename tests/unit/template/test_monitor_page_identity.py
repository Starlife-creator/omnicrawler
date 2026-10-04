from __future__ import annotations

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.templates.template_monitor import TemplateMonitor


def test_unrelated_pages_do_not_replace_each_others_structure_baseline(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: monitor, workspace: work}\nsource: {kind: crawl, seeds: [https://example.test/list]}\n", encoding="utf-8")
    monitor = TemplateMonitor(load_config(path))
    fields = {"title": {"selector": "h1"}}
    def observe(url, body, data):
        result = FetchResult(CrawlRequest(url), url, 200, {"content-type": "text/html"}, body.encode(), 0.1)
        return monitor.observe(result, [ExtractedRecord(url, "html", data)], fields)
    listing = "<html><body><ul class='list'><li><a class='next'>Next</a></li></ul></body></html>"
    detail = "<html><body><article class='detail'><h1>Title</h1><p>Body</p></article></body></html>"
    first = observe("https://example.test/list", listing, {"title": "List"})
    assert first is not None and first.status == "healthy"
    second = observe("https://example.test/detail", detail, {"title": "Title"})
    assert second is not None and second.status == "healthy"
    revisit = observe("https://example.test/list", listing, {"title": "List"})
    assert revisit is not None and revisit.structure_similarity == 1.0
    changed = observe("https://example.test/detail", "<html><body><form><input/></form></body></html>", {})
    assert changed is not None and changed.invalidated
