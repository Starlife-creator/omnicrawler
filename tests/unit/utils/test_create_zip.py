from __future__ import annotations

import zipfile
from pathlib import Path

from tools.create_zip import create_zip


def test_clean_source_archive_excludes_retained_portable_runtime(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("source readme", encoding="utf-8")
    (project / "OmniCrawler.exe").write_bytes(b"MZ-retained-portable-app")
    (project / "RUNTIME-MANIFEST.json").write_text("{}", encoding="utf-8")
    (project / "_internal").mkdir()
    (project / "_internal" / "runtime.bin").write_bytes(b"runtime")
    (project / "browsers").mkdir()
    (project / "browsers" / "chromium.bin").write_bytes(b"browser")
    (project / "runtime").mkdir()
    (project / "runtime" / "models.bin").write_bytes(b"model")
    (project / "data").mkdir()
    (project / "data" / "run-state.sqlite3").write_bytes(b"state")

    archive_path = tmp_path / "source.zip"
    create_zip(project, archive_path, "Demo", clean_source=True)

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        # F13：目录条目（以 / 结尾）是显式写入的；文件内容只应有 README.md
        file_names = {name for name in names if not name.endswith("/")}

    assert file_names == {"Demo/README.md"}
