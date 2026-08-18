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
