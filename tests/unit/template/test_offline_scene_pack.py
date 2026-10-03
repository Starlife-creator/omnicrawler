from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.extractors import HTMLProcessor
from omnicrawler.templates.template_catalog import TemplateCatalog
from omnicrawler.templates.template_health import TemplatePack


def test_offline_scene_pack_install_render_and_production_extract(tmp_path, monkeypatch, capsys):
    from omnicrawler.cli import main

    root = Path(__file__).resolve().parents[3]
    catalog = TemplateCatalog(root / "examples/task_reuse/templates")
    record = catalog.get("examples/public-list")
    assert record is not None
    monkeypatch.chdir(root / "examples/task_reuse")
    package = tmp_path / "scene.zip"
    main(["templates", "export-pack", "examples/public-list", "-o", str(package)])
    assert json.loads(capsys.readouterr().out)["created"] == str(package)
    main(["templates", "import-pack", str(package), "--target", str(tmp_path / "templates")])
    installed = [Path(value) for value in json.loads(capsys.readouterr().out)["created"]]
    target_catalog = TemplateCatalog(tmp_path / "templates")
    target_record = target_catalog.get("examples/public-list")
    assert target_record is not None
    assert installed[0].read_bytes() == record.path.read_bytes()
    rendered = target_catalog.render(target_record, {"seed_url": "https://example.test/new-list"})
    config_path = tmp_path / "task.yaml"
    config_path.write_text(yaml.safe_dump(rendered), encoding="utf-8")
    config = load_config(config_path)
    example = target_record.config["template"]["offline_example"]
    request = CrawlRequest(example["url"])
    result = FetchResult(request, request.url, 200, {"content-type": "text/html"}, example["html"].encode(), 0)
    assert [item.data for item in HTMLProcessor(config).process(result).records] == example["expected"]
    assert example["historical_reference_only"] and "offline_example" not in config.raw


def test_empty_export_and_empty_import_are_not_success(tmp_path):
    with pytest.raises(ValueError, match="空包"):
        TemplatePack.export([], tmp_path / "empty.zip")
    assert not (tmp_path / "empty.zip").exists()
    with zipfile.ZipFile(tmp_path / "empty.zip", "w") as archive:
        archive.writestr(TemplatePack.MANIFEST, json.dumps({"format": 1, "files": {}}))
    with pytest.raises(ValueError, match="manifest"):
        TemplatePack.import_pack(tmp_path / "empty.zip", tmp_path / "installed")


def test_unlisted_payload_rejected_before_any_import_write(tmp_path):
    payload = b"project: {name: public}\nsource: {kind: static_html, seeds: [https://example.test/]}\n"
    package = tmp_path / "hidden.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("templates/public.yaml", payload)
        archive.writestr("templates/hidden.yaml", payload)
        archive.writestr(TemplatePack.MANIFEST, json.dumps({"format": 1, "files": {"templates/public.yaml": hashlib.sha256(payload).hexdigest()}}))
    with pytest.raises(ValueError, match="unlisted"):
        TemplatePack.import_pack(package, tmp_path / "installed")
    assert not list((tmp_path / "installed").rglob("*.yaml"))


def test_pack_rejects_aliases_before_import(tmp_path):
    payload = b"project: &p {name: cycle}\nsource: *p\n"
    package = tmp_path / "alias.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("templates/cycle.yaml", payload)
        archive.writestr(TemplatePack.MANIFEST, json.dumps({"format": 1, "files": {"templates/cycle.yaml": hashlib.sha256(payload).hexdigest()}}))
    with pytest.raises(ValueError, match="aliases"):
        TemplatePack.import_pack(package, tmp_path / "installed")
