"""S2.1.3：.env 优先级修正 + 解析健壮化。

验收：项目级 .env 优先于用户级（源A P1#81 / 源B P1#18）；
脏 .env（非 UTF-8/行内注释/export 前缀/BOM）不抛裸异常（源B P2#67）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.core.ai_env import (
    AI_ENV_KEYS,
    load_ai_env,
    parse_env_file,
    save_ai_env,
)


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return home


def _write_env(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_s213_project_env_overrides_user_env(tmp_path: Path, isolated_home: Path) -> None:
    _write_env(isolated_home / ".omnicrawl" / ".env", "OMNICRAWL_AI_MODEL=user-model\n")
    project = tmp_path / "proj"
    _write_env(project / ".env", "OMNICRAWL_AI_MODEL=project-model\n")

    merged = load_ai_env(project)
    assert merged["OMNICRAWL_AI_MODEL"] == "project-model"


def test_s213_cwd_env_overrides_user_env(tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_env(isolated_home / ".omnicrawl" / ".env", "OMNICRAWL_AI_MODEL=user-model\n")
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    _write_env(cwd / ".env", "OMNICRAWL_AI_MODEL=cwd-model\n")
    monkeypatch.chdir(cwd)

    merged = load_ai_env()
    assert merged["OMNICRAWL_AI_MODEL"] == "cwd-model"


def test_s213_process_env_overrides_project_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    _write_env(project / ".env", "OMNICRAWL_AI_MODEL=file-model\n")
    monkeypatch.setenv("OMNICRAWL_AI_MODEL", "process-model")

    merged = load_ai_env(project)
    assert merged["OMNICRAWL_AI_MODEL"] == "process-model"


def test_s213_user_env_reachable_when_no_higher_layer(tmp_path: Path, isolated_home: Path) -> None:
    _write_env(isolated_home / ".omnicrawl" / ".env", "OMNICRAWL_AI_MODEL=user-model\n")
    merged = load_ai_env()
    assert merged["OMNICRAWL_AI_MODEL"] == "user-model"


def test_s213_dirty_encoding_does_not_raise(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_bytes(b"KEY=good\nBROKEN=\xff\xfe not utf-8\n")
    parsed = parse_env_file(path)
    assert parsed["KEY"] == "good"
    assert "BROKEN" in parsed


def test_s213_inline_comments_stripped_unless_quoted(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        'PLAIN=value # trailing note\nQUOTED="a # b" # note\nNONE=no-comment\n',
        encoding="utf-8",
    )
    parsed = parse_env_file(path)
    assert parsed["PLAIN"] == "value"
    assert parsed["QUOTED"] == "a # b"
    assert parsed["NONE"] == "no-comment"


def test_s213_export_prefix_supported(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("export OMNICRAWL_AI_PROVIDER=openai\n", encoding="utf-8")
    assert parse_env_file(path)["OMNICRAWL_AI_PROVIDER"] == "openai"


def test_s213_bom_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("\ufeffOMNICRAWL_AI_MODEL=bom-model\n", encoding="utf-8")
    assert parse_env_file(path)["OMNICRAWL_AI_MODEL"] == "bom-model"


def test_s213_quoted_unescape_regression(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text('KEY="a \\"quoted\\" \\\\ path"\n', encoding="utf-8")
    assert parse_env_file(path)["KEY"] == 'a "quoted" \\ path'


def test_s213_save_updates_export_style_line(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("export OMNICRAWL_AI_MODEL=old\n", encoding="utf-8")
    saved = save_ai_env({"OMNICRAWL_AI_MODEL": "new"}, project_root=tmp_path)
    assert saved == path
    content = path.read_text(encoding="utf-8")
    assert content.count("OMNICRAWL_AI_MODEL") == 1
    assert parse_env_file(path)["OMNICRAWL_AI_MODEL"] == "new"


def test_s213_non_ai_keys_survive_priority_merge(tmp_path: Path, isolated_home: Path) -> None:
    _write_env(isolated_home / ".omnicrawl" / ".env", "CUSTOM_USER=1\n")
    project = tmp_path / "proj"
    _write_env(project / ".env", "CUSTOM_PROJECT=1\nOMNICRAWL_AI_MODEL=m\n")
    merged = load_ai_env(project)
    assert merged["CUSTOM_PROJECT"] == "1"
    assert merged["CUSTOM_USER"] == "1"
    for key in AI_ENV_KEYS:
        if key in merged:
            assert merged[key]


if __name__ == "__main__":
    pytest.main([__file__])

