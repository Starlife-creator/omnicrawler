from __future__ import annotations

import yaml

from omnicrawl.core.config import load_config
from omnicrawl.pipeline.exporters import export_all
from omnicrawl.state import StateStore


def test_optional_analytics_outputs_have_explicit_fallback(tmp_path) -> None:
    config_path = tmp_path / "project.yaml"
    config_path.write_text(yaml.safe_dump({
        "project": {"name": "optional", "workspace": str(tmp_path / "work")},
        "source": {"kind": "static_html", "seeds": ["https://example.org"]},
        "http": {"user_agent": "OptionalTest/1.0 (+contact: test@example.org)"},
        "outputs": {"jsonl": False, "csv": False, "xlsx": False, "parquet": True, "duckdb": True},
    }), encoding="utf-8")
    config = load_config(config_path)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run_id = state.start_run("optional", str(config_path))
        summary = export_all(config, state, run_id)

    for backend in ("parquet", "duckdb"):
        supported = backend in summary["files"]
        explained = any(backend.casefold() in warning.casefold() for warning in summary["warnings"])
        assert supported or explained
