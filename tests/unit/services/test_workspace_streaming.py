"""S2.5.17：workspace 流式打包（不整读多 GB 内存 + 排除 SQLite/旧导出）。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.services.workspace import WorkspaceManager


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: ws, workspace: '{tmp_path / 'work'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    return path


def test_full_package_streams_large_files_and_excludes_output(tmp_path: Path) -> None:
    from omnicrawler.state import StateStore

    manager = WorkspaceManager(load_config(_config(tmp_path)))
    manager.initialize()
    raw = manager.root / "raw" / "big.bin"
    raw.write_bytes(b"0123456789" * 200_000)  # 2MB
    (manager.root / "output" / "old_export.csv").write_text("a,b\n", encoding="utf-8")
    with StateStore(manager.root / "state.sqlite3"):
        pass

    result = manager.package(tmp_path / "full.zip", kind="full")
    with zipfile.ZipFile(result["created"]) as archive:
        names = archive.namelist()
        assert "project/workspace/raw/big.bin" in names
        assert not any(name.startswith("project/workspace/output/") for name in names)
        assert archive.read("project/workspace/raw/big.bin") == raw.read_bytes()
        assert "project/workspace/state.sqlite3" in names
    assert result["files"] >= 3


def test_full_package_manifest_hashes_match_contents(tmp_path: Path) -> None:
    manager = WorkspaceManager(load_config(_config(tmp_path)))
    manager.initialize()
    (manager.root / "raw" / "x.txt").write_text("hello", encoding="utf-8")
    result = manager.package(tmp_path / "full.zip", kind="full")
    with zipfile.ZipFile(result["created"]) as archive:
        manifest = json.loads(archive.read("omnicrawler-package.json").decode("utf-8"))
        assert manifest["kind"] == "full-workspace"
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
