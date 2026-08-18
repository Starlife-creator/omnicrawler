"""S2.1.1 ③④⑤：GUI 校验与序列化契约测试。

④ validate_full_config 转调核心 validate_config（单一校验真源）；
⑤ GUI to_yaml 产物必须能被核心 load_config 接受。
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("ruamel") is None,
    reason="GUI YAML round-trip requires the optional ruamel.yaml dependency",
)


def _gui_config() -> object:
    from omnicrawler.gui.core.config_serializer import from_yaml

    return from_yaml(
        "project: {name: gui-t, workspace: work}\n"
        "source: {kind: crawl, seeds: [https://example.com/]}\n"
        "crawl: {max_pages: 20, concurrency: 2}\n"
    )


def test_s211_validate_full_config_passes_for_clean_config() -> None:
    from omnicrawler.gui.core.validator import validate_full_config

    config = _gui_config()
    config.user_agent = "OmniCrawler-Test/1.0 (+contact: tester@example.org)"
    errors, warnings = validate_full_config(config)
    assert errors == []
    assert warnings == []


def test_s211_validate_full_config_propagates_core_warnings() -> None:
    from omnicrawler.gui.core.validator import validate_full_config

    config = _gui_config()
    config.user_agent = "Bot/1.0 (+contact: change-me@example.com)"
    errors, warnings = validate_full_config(config)
    assert errors == []
    assert any("User-Agent" in item for item in warnings)


def test_s211_validate_full_config_reports_core_errors() -> None:
    from omnicrawler.gui.core.validator import validate_full_config

    config = _gui_config()
    config.user_agent = "Bot/1.0 (+contact: tester@example.org)"
    config.passthrough.setdefault("http", {})["engine"] = "requests"
    errors, warnings = validate_full_config(config)
    assert any("http.engine只能是urllib或httpx_async" in item for item in errors)
    assert not any("engine" in item for item in warnings)


def test_s211_validate_full_config_skips_core_when_no_seeds() -> None:
    from omnicrawler.gui.core.config_serializer import from_yaml
    from omnicrawler.gui.core.validator import validate_full_config

    config = from_yaml("project: {name: t, workspace: work}\n")
    errors, warnings = validate_full_config(config)
    assert not any("source.seeds" in item for item in errors)


def test_s211_gui_yaml_output_accepted_by_core_load_config(tmp_path) -> None:
    from omnicrawler.core.config import load_config
    from omnicrawler.gui.core.config_serializer import to_yaml

    config = _gui_config()
    config.user_agent = "OmniCrawler-Test/1.0 (+contact: tester@example.org)"
    config.source_kind = "crawl"
    path = tmp_path / "gui_out.yaml"
    path.write_text(to_yaml(config), encoding="utf-8")
    loaded = load_config(path)
    assert loaded.source_kind == "crawl"
    assert loaded.raw["crawl"]["max_pages"] == 20
    assert loaded.section("http")["engine"] == "urllib"


if __name__ == "__main__":
    pytest.main([__file__])
