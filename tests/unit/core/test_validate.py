"""S2.1.1：配置校验单一真源——顶层段白名单 + strict 模式 + 拼写候选提示。

验收：未知键默认 warning 不拦截；strict=True 时升级为 error；
difflib 给出"是否想写"候选；DEFAULTS 补全 http.engine / processors.pdf.ocr_backend。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.core.config import DEFAULTS, AppConfig, deep_merge, load_config, validate_config


def _config(**overrides: object) -> AppConfig:
    raw = deep_merge(
        DEFAULTS,
        {
            "project": {"name": "t", "workspace": "work"},
            "source": {"kind": "crawl", "seeds": ["https://example.com/"]},
            **overrides,
        },
    )
    return AppConfig(Path("<memory>"), Path.cwd(), raw, Path.cwd())


def test_s211_unknown_top_level_section_warns_by_default() -> None:
    config = _config(projext={"name": "x"})
    errors, warnings = validate_config(config)
    assert errors == []
    assert any("未知顶层段 'projext'" in item for item in warnings)


def test_s211_unknown_top_level_section_errors_in_strict_mode() -> None:
    config = _config(projext={"name": "x"})
    errors, warnings = validate_config(config, strict=True)
    assert any("未知顶层段 'projext'" in item for item in errors)
    assert "未知顶层段" not in " ".join(warnings)


def test_s211_unknown_section_suggests_closest_known() -> None:
    config = _config(soource={"kind": "crawl"})
    _errors, warnings = validate_config(config)
    assert any("是否想写 'source'" in item for item in warnings)


def test_s211_typo_in_crawl_field_warns() -> None:
    config = _config(crawl={"maxpages": 5})
    errors, warnings = validate_config(config)
    assert errors == []
    assert any("配置段 'crawl' 包含未知字段 'maxpages'" in item for item in warnings)


def test_s211_typo_in_http_field_warns() -> None:
    config = _config(http={"user_agentx": "Bot/1.0"})
    errors, warnings = validate_config(config)
    assert errors == []
    assert any("配置段 'http' 包含未知字段 'user_agentx'" in item for item in warnings)


def test_s211_typo_in_outputs_field_warns() -> None:
    config = _config(outputs={"json1": True})
    errors, warnings = validate_config(config)
    assert errors == []
    assert any("配置段 'outputs' 包含未知字段 'json1'" in item for item in warnings)


def test_s211_typo_in_resources_field_warns() -> None:
    config = _config(resources={"profiel": "balanced"})
    errors, warnings = validate_config(config)
    assert errors == []
    assert any("配置段 'resources' 包含未知字段 'profiel'" in item for item in warnings)
    assert any("是否想写 'profile'" in item for item in warnings)


def test_s211_typo_in_browser_field_warns() -> None:
    config = _config(browser={"headlesss": True})
    errors, warnings = validate_config(config)
    assert errors == []
    assert any("配置段 'browser' 包含未知字段 'headlesss'" in item for item in warnings)


def test_s211_typo_in_egress_field_warns() -> None:
    config = _config(egress={"maximum_reqeusts": 1})
    errors, warnings = validate_config(config)
    assert errors == []
    assert any("配置段 'egress' 包含未知字段 'maximum_reqeusts'" in item for item in warnings)


def test_s211_typo_in_nested_storage_field_warns() -> None:
    config = _config(storage={"records": {"backends": [], "max_errrors": 10}})
    errors, warnings = validate_config(config)
    assert errors == []
    assert any("配置段 'storage.records' 包含未知字段 'max_errrors'" in item for item in warnings)


def test_s211_strict_mode_accepts_clean_config() -> None:
    config = _config(http={"user_agent": "Test/1.0 (+contact: owner@example.org)"})
    errors, warnings = validate_config(config, strict=True)
    assert errors == []
    assert warnings == []


def test_s211_defaults_expose_http_engine_and_pdf_ocr_backend() -> None:
    assert DEFAULTS["http"]["engine"] == "urllib"
    assert DEFAULTS["processors"]["pdf"]["ocr_backend"] == "none"
    config = _config()
    assert config.section("http")["engine"] == "urllib"
    assert config.section("processors")["pdf"]["ocr_backend"] == "none"


def test_s211_unknown_engine_value_is_explicit_error() -> None:
    config = _config(http={"engine": "requests"})
    errors, _warnings = validate_config(config)
    assert any("http.engine只能是urllib或httpx_async" in item for item in errors)


def test_s211_dynamic_sections_not_flagged() -> None:
    config = _config(
        ai={"mode": "cloud", "providers": {"my_custom_vendor": {"base_url": "x"}}},
        auth={"options": {"any_custom": True}},
        source={"kind": "crawl", "seeds": ["https://example.com/"], "pagination": {"custom_legacy_key": 1}},
        transformers=[{"kind": "custom", "whatever": True}],
    )
    errors, warnings = validate_config(config)
    assert errors == []
    assert not any("未知" in item for item in warnings)


def test_s211_template_task_and_api_discovery_sections_allowed() -> None:
    config = _config(
        template={"id": "cms/example"},
        task={"some": "meta"},
        api_discovery={"enabled": True},
    )
    errors, warnings = validate_config(config)
    assert errors == []
    assert not any("未知" in item for item in warnings)


def test_s211_load_config_keeps_warnings_for_typo(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(
        "project: {name: t, workspace: work}\n"
        "source: {kind: crawl, seeds: [https://example.com/]}\n"
        "crawl: {maxpages: 5}\n",
        encoding="utf-8",
    )
    loaded = load_config(path)
    errors, warnings = validate_config(loaded)
    assert errors == []
    assert any("未知字段 'maxpages'" in item for item in warnings)


def test_s211_seed_completion_never_mints_drive_path_urls() -> None:
    from omnicrawl.cli._main import _complete_seed_scheme

    assert _complete_seed_scheme("example.com") == "https://example.com"
    assert _complete_seed_scheme("http://example.com/a") == "http://example.com/a"
    assert _complete_seed_scheme("https://example.com") == "https://example.com"
    assert _complete_seed_scheme(r"C:\data\page.html") is None
    assert _complete_seed_scheme(r"D:/data/page.html") is None
    assert _complete_seed_scheme("/absolute/path") is None
    assert _complete_seed_scheme("relative/path") == "https://relative/path"
    assert _complete_seed_scheme("") is None


def test_s211_quick_mode_keeps_explicit_require_features() -> None:
    from omnicrawl.core.capabilities import capability_report

    report = capability_report(mode="quick", require_features=["pdf", "core"])
    assert report["check"]["requested_features"] == ["pdf", "core"]
    assert report["check"]["features"]["core"]["ready"] is True
    assert report["check"]["features"]["pdf"]["ready"] is True
    report_default = capability_report(mode="quick")
    assert report_default["check"]["requested_features"] == ["core"]


if __name__ == "__main__":
    pytest.main([__file__])
