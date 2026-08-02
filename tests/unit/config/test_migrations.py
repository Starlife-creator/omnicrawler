from __future__ import annotations

import yaml

from omnicrawl.core.config import load_config
from omnicrawl.core.migrations import CURRENT_CONFIG_VERSION, migrate_file


def test_legacy_config_is_migrated_without_losing_unknown_fields(tmp_path) -> None:
    old = tmp_path / "old.yaml"
    old.write_text(yaml.safe_dump({
        "seed_urls": ["https://example.org/feed.xml"],
        "source": {"kind": "rss"},
        "crawl": {"delay_seconds": 2},
        "output": {"csv": False},
        "plugin_paths": ["plugins/old.py"],
        "vendor_extension": {"keep": True},
    }), encoding="utf-8")
    migrated = tmp_path / "new.yaml"
    _path, notes = migrate_file(old, migrated)
    data = yaml.safe_load(migrated.read_text(encoding="utf-8"))

    assert data["config_version"] == CURRENT_CONFIG_VERSION
    assert data["source"] == {"kind": "feed", "seeds": ["https://example.org/feed.xml"]}
    assert data["http"]["delay_seconds"] == 2
    assert data["vendor_extension"]["keep"] is True
    assert notes

    config = load_config(migrated)
    assert config.source_kind == "feed"
    assert config.raw["plugin_paths"] == ["plugins/old.py"]
