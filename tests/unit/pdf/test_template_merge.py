from __future__ import annotations

from pathlib import Path

import yaml

from omnicrawler.pdfx.project import create_project_config
from omnicrawler.pdfx.template_merge import merge_project_template, merge_template_config


def _template(path: Path, pattern: str, engine: str = "regex") -> None:
    path.write_text(yaml.safe_dump({
        "project_name": "base", "input_dir": "in", "work_dir": "work", "output_dir": "out",
        "ocr": {"backend": "none"},
        "extraction": {"workers": 1, "engine": engine},
        "fields": [{"name": "code", "label": "Code", "patterns": [pattern]}],
    }), encoding="utf-8")


def test_template_merge_applies_upstream_only_and_preserves_project_paths(tmp_path: Path):
    base, current, new = (tmp_path / name for name in ("base.yaml", "project.yaml", "new.yaml"))
    _template(base, "A")
    create_project_config(base, current, project_name="mine", input_dir=tmp_path / "input",
                          work_dir=tmp_path / "work", output_dir=tmp_path / "out")
    _template(new, "B", "deterministic")
    result = merge_project_template(current, base, new, apply=True)
    assert result["applied_to_file"] is True
    assert not result["conflicts"]
    assert "fields" in result["applied"]
    merged = yaml.safe_load(current.read_text(encoding="utf-8"))
    assert merged["fields"][0]["patterns"] == ["B"]
    assert merged["extraction"]["engine"] == "deterministic"
    assert merged["project_name"] == "mine"


def test_template_conflict_keeps_local_value_and_preview_does_not_write(tmp_path: Path):
    base, current, new = (tmp_path / name for name in ("base.yaml", "project.yaml", "new.yaml"))
    _template(base, "A")
    _template(new, "B")
    _template(current, "LOCAL")
    before = current.read_bytes()
    result = merge_project_template(current, base, new)
    assert result["conflicts"] == ["fields"]
    assert current.read_bytes() == before
    merge_project_template(current, base, new, apply=True)
    assert yaml.safe_load(current.read_text(encoding="utf-8"))["fields"][0]["patterns"] == ["LOCAL"]


def test_cli_uses_snapshotted_base_and_reports_conflicts(tmp_path: Path):
    import contextlib
    import io
    import json

    import pytest

    from omnicrawler.pdfx.cli import main

    base, current, new = (tmp_path / name for name in ("base.yaml", "project.yaml", "new.yaml"))
    _template(base, "A")
    create_project_config(base, current, project_name="mine", input_dir=tmp_path / "input",
                          work_dir=tmp_path / "work", output_dir=tmp_path / "out")
    data = yaml.safe_load(current.read_text(encoding="utf-8"))
    data["fields"][0]["patterns"] = ["LOCAL"]
    current.write_text(yaml.safe_dump(data), encoding="utf-8")
    _template(new, "B")
    output = io.StringIO()
    import sys
    from unittest.mock import patch
    argv = ["pdfx", "--config", str(current), "merge-template", "--template", str(new)]
    with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output), pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    result = json.loads(output.getvalue())["result"]
    assert result["conflicts"] == ["fields"]


def test_template_merge_does_not_alias_loaded_yaml_documents(tmp_path: Path):
    base, current, new = (tmp_path / name for name in ("base.yaml", "project.yaml", "new.yaml"))
    _template(base, "A")
    _template(current, "A")
    new_data = yaml.safe_load(base.read_text(encoding="utf-8"))
    new_data["extraction"]["cache"] = True
    new.write_text(yaml.safe_dump(new_data), encoding="utf-8")
    result = merge_template_config(current, base, new)
    assert result["applied"] == ["extraction.cache"]
    assert result["config"]["extraction"]["workers"] == 1


def test_new_nested_option_merges_without_touching_local_worker_count(tmp_path: Path):
    base, current, new = (tmp_path / name for name in ("base.yaml", "project.yaml", "new.yaml"))
    _template(base, "A")
    _template(new, "A")
    new_data = yaml.safe_load(new.read_text(encoding="utf-8"))
    new_data["extraction"]["cache"] = True
    new.write_text(yaml.safe_dump(new_data), encoding="utf-8")
    current_data = yaml.safe_load(base.read_text(encoding="utf-8"))
    current_data["extraction"]["workers"] = 3
    current.write_text(yaml.safe_dump(current_data), encoding="utf-8")
    result = merge_project_template(current, base, new, apply=True)
    assert result["applied"] == ["extraction.cache"]
    merged = yaml.safe_load(current.read_text(encoding="utf-8"))
    assert merged["extraction"]["workers"] == 3 and merged["extraction"]["cache"] is True
