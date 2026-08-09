from pathlib import Path

import pytest
import yaml

from omnicrawl.core.config import load_config


def _write_config(path: Path, diagnostics: object | None = None) -> Path:
    payload = {
        "project": {"name": "diagnostics-test", "workspace": "work/test"},
        "source": {"kind": "static_html", "seeds": ["https://example.test/"]},
    }
    if diagnostics is not None:
        payload["diagnostics"] = diagnostics
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_diagnostics_defaults_are_available_to_runtime(tmp_path):
    config = load_config(_write_config(tmp_path / "config.yaml"))

    assert config.section("diagnostics") == {
        "retention_days": 30,
        "max_files": 500,
        "max_bytes": 500 * 1024 * 1024,
    }


@pytest.mark.parametrize(
    "diagnostics, expected",
    [
        ({"retention_days": 0}, "diagnostics.retention_days"),
        ({"max_files": "many"}, "diagnostics.max_files必须是整数"),
        ({"max_bytes": 100}, "diagnostics.max_bytes"),
        ({"unexpected": True}, "diagnostics包含未知字段"),
        ("invalid", "diagnostics必须是YAML对象"),
    ],
)
def test_invalid_diagnostics_config_is_rejected(tmp_path, diagnostics, expected):
    with pytest.raises(ValueError, match=expected):
        load_config(_write_config(tmp_path / "config.yaml", diagnostics))
