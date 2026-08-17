from __future__ import annotations

import os
import time

import yaml

from omnicrawler.core.config import load_config
from omnicrawler.services.retention import apply_retention, plan_retention


def test_retention_is_dry_run_until_explicitly_applied(tmp_path) -> None:
    workspace = tmp_path / "work"
    raw = workspace / "raw" / "old.html"
    raw.parent.mkdir(parents=True)
    raw.write_text("old", encoding="utf-8")
    old = time.time() - 10 * 86400
    os.utime(raw, (old, old))
    config_path = tmp_path / "project.yaml"
    config_path.write_text(yaml.safe_dump({
        "project": {"name": "retention", "workspace": str(workspace)},
        "source": {"kind": "static_html", "seeds": ["https://example.org"]},
        "storage": {"retention": {"raw_days": 7}},
        "http": {"user_agent": "RetentionTest/1.0 (+contact: test@example.org)"},
    }), encoding="utf-8")
    config = load_config(config_path)

    plan = plan_retention(config)
    assert [item.path for item in plan] == [raw]
    assert raw.exists()
    result = apply_retention(config, plan)
    assert not raw.exists()
    assert result["total_bytes"] == 3
    assert (workspace / "output" / "retention_audit.jsonl").is_file()
