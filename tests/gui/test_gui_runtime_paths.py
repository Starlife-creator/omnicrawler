import sys
from pathlib import Path
from unittest.mock import patch

from omnicrawler import runtime_paths


def test_source_application_dir_is_project_root() -> None:
    assert (runtime_paths.application_dir() / "pyproject.toml").is_file()


def test_source_user_guide_is_found() -> None:
    # 用户指南已合并为 OmniCrawler-用户指南.md，位于项目根目录
    assert runtime_paths.find_document("OmniCrawler-用户指南.md", "USER_GUIDE.md") == (
        runtime_paths.application_dir() / "OmniCrawler-用户指南.md"
    )


def test_frozen_build_prefers_companion_cli(tmp_path: Path) -> None:
    suffix = ".exe" if sys.platform == "win32" else ""
    gui = tmp_path / f"OmniCrawler{suffix}"
    cli = tmp_path / f"omnicrawler{suffix}"
    gui.touch()
    cli.touch()
    with (
        patch.object(runtime_paths.sys, "frozen", True, create=True),
        patch.object(runtime_paths.sys, "executable", str(gui)),
    ):
        assert runtime_paths.resolve_cli_command("omnicrawler") == str(cli)


def test_frozen_document_is_found_next_to_executable(tmp_path: Path) -> None:
    gui = tmp_path / "OmniCrawler.exe"
    guide = tmp_path / "docs" / "USER_GUIDE.md"
    guide.parent.mkdir()
    guide.write_text("guide", encoding="utf-8")
    with (
        patch.object(runtime_paths.sys, "frozen", True, create=True),
        patch.object(runtime_paths.sys, "executable", str(gui)),
    ):
        assert runtime_paths.find_document("USER_GUIDE.md") == guide
