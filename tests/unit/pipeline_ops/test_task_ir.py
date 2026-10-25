"""C-#16：``pipeline_ops.task_ir`` 校验 / 能力推断 / 域名提取 / 片段编译直接单测。"""
from __future__ import annotations

import pytest

from omnicrawler.pipeline_ops.task_ir import (
    TaskIR,
    api_candidate_fragment,
    infer_capabilities,
    recording_fragment,
    source_domains,
)


def test_from_mapping_preserves_unknown_keys_in_extensions() -> None:
    ir = TaskIR.from_mapping({
        "source": {"seeds": ["https://example.com"]},
        "custom_section": {"note": "kept"},
    })
    assert ir.extensions["custom_section"] == {"note": "kept"}


def test_from_mapping_rejects_non_list_actions() -> None:
    with pytest.raises(ValueError, match="actions必须是数组"):
        TaskIR.from_mapping({"actions": {"click": "x"}})


def test_from_mapping_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="高于当前支持"):
        TaskIR.from_mapping({"ir_version": 99, "source": {"seeds": ["https://example.com"]}})


def test_capabilities_coerced_to_strings() -> None:
    ir = TaskIR.from_mapping({"source": {"seeds": ["https://example.com"]}, "capabilities": [1, "browser"]})
    assert ir.capabilities == ["1", "browser"]


def test_infer_capabilities_matches_decls() -> None:
    assert "browser" in infer_capabilities({"source": {"kind": "browser"}})
    assert "ocr" in infer_capabilities({"processors": {"pdf": {"enabled": True, "skip_ocr": False}}})
    assert "ai" in infer_capabilities({"ai": {"mode": "local"}})
    assert "storage" in infer_capabilities({"storage": {"objects": {"backend": "s3"}}})
    assert "plugin" in infer_capabilities({"plugins": {"enabled": True}})
    assert "stream" in infer_capabilities({"source": {"kind": "websocket"}})


def test_default_plugin_search_paths_do_not_count_as_plugin() -> None:
    capabilities = infer_capabilities({"plugins": {"paths": ["plugins/", "plugins_installed/"]}})
    assert "plugin" not in capabilities


def test_source_domains_dedupes_casefold_and_includes_login_url() -> None:
    ir = TaskIR.from_mapping({
        "source": {"seeds": ["https://Example.com/a", "https://example.com/b"]},
        "authorization": {"url": "https://login.EXAMPLE.com/session"},
    })
    assert source_domains(ir) == ["example.com", "login.example.com"]


def test_recording_fragment_requires_action_list_and_marks_browser() -> None:
    with pytest.raises(ValueError, match="actions必须是数组"):
        recording_fragment({"actions": {}})

    fragment = recording_fragment({
        "actions": [{"type": "click"}],
        "start_url": "https://example.com/start",
        "api_responses": [{"url": "https://example.com/api"}],
    })
    assert fragment["capabilities"] == ["browser"]
    assert fragment["source"]["seeds"] == ["https://example.com/start"]
    assert fragment["extensions"]["api_candidates"] == [{"url": "https://example.com/api"}]


def test_api_candidate_fragment_validates_url_and_method_whitelist() -> None:
    with pytest.raises(ValueError, match="缺少url"):
        api_candidate_fragment({})
    with pytest.raises(ValueError, match="不支持的 HTTP method"):
        api_candidate_fragment({"url": "https://example.com", "method": "TRACE"})

    fragment = api_candidate_fragment({
        "url": "https://example.com/api", "method": "post",
        "headers": {"X-Test": "1"}, "body": {"k": "v"},
        "item_path": "$.items[*]", "pagination": {"mode": "page"},
    })
    assert fragment["source"]["method"] == "POST"
    assert fragment["source"]["headers"] == {"X-Test": "1"}
    assert fragment["pagination"] == {"mode": "page"}
    assert fragment["extensions"]["api_candidate_evidence"]["url"] == "https://example.com/api"


def test_from_config_round_trip_restores_seeds() -> None:
    ir = TaskIR.from_config({
        "project": {"name": "round-trip"},
        "source": {"kind": "crawl", "seeds": ["https://example.com"]},
        "crawl": {"max_pages": 4, "same_host": True},
    })
    restored = ir.to_config()
    assert restored["source"]["seeds"] == ["https://example.com"]
    assert restored["crawl"]["max_pages"] == 4
