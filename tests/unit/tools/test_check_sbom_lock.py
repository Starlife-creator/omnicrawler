from __future__ import annotations

import json
from pathlib import Path

from tools.check_sbom_lock import check


def _write_inputs(tmp_path: Path, components: list[dict[str, str]]) -> tuple[Path, Path]:
    sbom = tmp_path / "SBOM.json"
    sbom.write_text(json.dumps({"components": components}), encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n\n'
        '[[package]]\nname = "demo-lib"\nversion = "2.4.0"\n',
        encoding="utf-8",
    )
    return sbom, lock


def test_check_accepts_normalised_name_and_locked_version(tmp_path: Path) -> None:
    sbom, lock = _write_inputs(tmp_path, [{"name": "Demo_Lib", "version": "2.4.0"}])
    assert check(sbom, lock) == []


def test_check_rejects_unlocked_and_drifted_components(tmp_path: Path) -> None:
    sbom, lock = _write_inputs(
        tmp_path,
        [
            {"name": "demo-lib", "version": "2.5.0"},
            {"name": "untracked", "version": "1.0.0"},
        ],
    )
    issues = check(sbom, lock)
    assert any("版本偏离" in issue for issue in issues)
    assert any("未进入" in issue for issue in issues)


def test_check_rejects_empty_sbom(tmp_path: Path) -> None:
    sbom, lock = _write_inputs(tmp_path, [])
    assert check(sbom, lock) == ["SBOM components 为空"]
