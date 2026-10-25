"""B-P3-10：doc_extractors 公共基类抽取回归——三个提取器共享隔离样板，行为保持。"""

from __future__ import annotations

from omnicrawler.doc_extractors.base import BaseDocExtractor, JSONDocExtractor, TextDocExtractor
from omnicrawler.doc_extractors.html import HTMLDocExtractor
from omnicrawler.state.scene_store import SlotDefinition


def test_extractors_share_base_class() -> None:
    for extractor in (TextDocExtractor(), JSONDocExtractor(), HTMLDocExtractor()):
        assert isinstance(extractor, BaseDocExtractor)


def test_text_extractor_good_definition_hits() -> None:
    definitions = [
        SlotDefinition(scene="t", slot_key="good", extractor_type="text", pattern="alpha"),
    ]
    hits = TextDocExtractor().extract("alpha beta", definitions)
    assert hits[0].value == "alpha"


def test_shared_isolation_keeps_good_json_definition_running() -> None:
    definitions = [
        SlotDefinition(scene="t", slot_key="good", extractor_type="jsonpath", pattern="$.title"),
        SlotDefinition(scene="t", slot_key="broken", extractor_type="jsonpath", pattern="$..[bad"),
    ]
    hits = JSONDocExtractor().extract({"title": "hello"}, definitions)
    by_key = {hit.slot_key: hit for hit in hits}
    assert by_key["good"].value == "hello"
    assert by_key["broken"].value is None
    assert by_key["broken"].confidence == 0.0
    assert by_key["broken"].evidence["error_type"]
