from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.templates.template_catalog import bundled_template_catalog
from omnicrawler.templates.template_health import StructureSnapshot, TemplatePack, validate_catalog


def test_new_catalog_templates_pass_offline_health_checks() -> None:
    results = validate_catalog(bundled_template_catalog())
    failures = {result.template_id: result.errors for result in results if not result.ok}
    assert not failures


def test_structure_snapshot_detects_large_drift() -> None:
    old = StructureSnapshot.from_html("generic/demo", "https://example.org", '<main id="content"><article class="story">x</article></main>')
    new = StructureSnapshot.from_html("generic/demo", "https://example.org", '<div id="app"><canvas class="shell"></canvas></div>')
    assert new.similarity(old) < 0.5


def test_template_pack_round_trip_and_no_overwrite(tmp_path: Path) -> None:
    catalog = bundled_template_catalog()
    record = catalog.get("generic/single-page")
    assert record is not None
    pack = TemplatePack.export([record], tmp_path / "pack.zip")
    destination = tmp_path / "templates"
    created = TemplatePack.import_pack(pack, destination)

    assert created == [destination / "generic" / "single-page.yaml"]
    with pytest.raises(FileExistsError):
        TemplatePack.import_pack(pack, destination)


# ── B2：UA 诚实性在配置入口的守卫（模板硬编码浏览器 UA 必须 error）────

_PROBE_YAML = """\
template:
  id: probe/ua-guard
  name: UA 守卫探针
  category: probe
  description: 探针模板（仅测试用）
  version: 1.0.0
project: {{name: ua_probe, workspace: work/ua_probe}}
source: {{kind: static_html, seeds: ['https://example.org/']}}
http:
{ua_line}  respect_robots: true
"""

_CHROME_UA_LINE = (
    '  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"\n'
)


def _probe_record(tmp_path: Path, ua_line: str):
    from omnicrawler.templates.template_catalog import TemplateCatalog
    from omnicrawler.templates.template_health import validate_template

    probe = tmp_path / "probe.yaml"
    probe.write_text(_PROBE_YAML.format(ua_line=ua_line), encoding="utf-8")
    records = TemplateCatalog(tmp_path).discover()
    assert len(records) == 1
    return validate_template(records[0])


def test_validate_template_rejects_browser_spoofed_user_agent(tmp_path: Path) -> None:
    """历史缺陷对照：5 个 social 模板曾硬编码 Chrome/127 并经 browser_pool 生效。"""
    health = _probe_record(tmp_path, _CHROME_UA_LINE)
    assert not health.ok
    assert health.errors, "缺自报标识或命中伪造签名，二者必居其一"


def test_validate_template_rejects_chrome_signature_even_with_honest_prefix(tmp_path: Path) -> None:
    """更锐的判据：即便含 OmniCrawler/{ver} 前缀，Chrome 精确签名也必须被拒。"""
    from omnicrawler._version import __version__

    ua_line = (
        f"  user_agent: \"OmniCrawler/{__version__} Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36\"\n"
    )
    health = _probe_record(tmp_path, ua_line)
    assert not health.ok
    assert any("浏览器伪造签名" in message for message in health.errors)


def test_validate_template_allows_honest_or_absent_user_agent(tmp_path: Path) -> None:
    health = _probe_record(tmp_path, "")
    assert health.ok, health.errors
