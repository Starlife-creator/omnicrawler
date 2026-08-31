"""B05-010/B09-003：配置路径 contained-in-root 校验回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.config import load_config, require_config_path, validate_config


def _write_config(root: Path, *, workspace: str = "work/p", extra: str = "") -> Path:
    cfg = root / "task.yaml"
    cfg.write_text(
        "project:\n"
        f"  name: p\n"
        f"  workspace: {workspace}\n"
        "source:\n"
        "  kind: static_html\n"
        "  seeds: [https://example.org/]\n"
        "outputs:\n"
        "  jsonl: true\n"
        "  csv: true\n"
        "  xlsx: true\n"
        f"{extra}",
        encoding="utf-8",
    )
    return cfg


def _outside_test_root(path: Path) -> Path:
    """Return an absolute path outside any repository detected above tmp_path."""
    return Path(path.anchor) / f"omnicrawler-test-outside-{path.name}"


def test_workspace_inside_root_no_warning(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, workspace="work/p")
    loaded = load_config(cfg)
    assert not [w for w in loaded.warnings if "项目根之外" in w]


def test_workspace_outside_root_warns(tmp_path: Path) -> None:
    outside = _outside_test_root(tmp_path)
    cfg = _write_config(tmp_path, workspace=str(outside / "shared"))
    loaded = load_config(cfg)
    assert any("project.workspace" in w and "项目根之外" in w for w in loaded.warnings)


def test_workspace_outside_root_strict_errors(tmp_path: Path) -> None:
    outside = _outside_test_root(tmp_path)
    cfg = _write_config(tmp_path, workspace=str(outside / "shared"))
    loaded = load_config(cfg)
    errors, _warnings = validate_config(loaded, strict=True)
    assert any("project.workspace" in e and "项目根之外" in e for e in errors)


def test_storage_local_directory_outside_root_warns(tmp_path: Path) -> None:
    outside = _outside_test_root(tmp_path)
    cfg = _write_config(
        tmp_path,
        extra=(
            "storage:\n"
            "  objects:\n"
            f"    local_directory: {outside / 'blobs'}\n"
        ),
    )
    loaded = load_config(cfg)
    assert any("storage.objects.local_directory" in w and "项目根之外" in w for w in loaded.warnings)


def test_require_config_path_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="任务配置不存在"):
        require_config_path(tmp_path / "nope.yaml")


def test_require_config_path_inside_cwd_ok(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert require_config_path("task.yaml", require_inside_cwd=True) == cfg.resolve()


def test_require_config_path_outside_cwd_rejected(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    with pytest.raises(ValueError, match="当前目录内"):
        require_config_path(cfg, require_inside_cwd=True)
