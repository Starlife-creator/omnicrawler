"""会话快照的**列举与删除**（U3 会话列表的底层）。

判据重点：解析失败的快照要**如实标注**而不是消失；删除的合法范围由代码约束。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.fetching.session_state import (
    SESSIONS_DIRNAME,
    STORAGE_STATE_SUFFIX,
    list_sessions,
    remove_session,
    session_name,
    storage_state_path,
    summarize_session,
)


def _write(workspace: Path, name: str, cookies: list[dict[str, object]]) -> Path:
    path = workspace / SESSIONS_DIRNAME / f"{name}{STORAGE_STATE_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")
    return path


def test_list_sessions_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert list_sessions(tmp_path / "no-workspace") == ()


def test_list_sessions_reports_metadata_only(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        session_name("alice|"),
        [
            {"name": "sid", "value": "SECRET", "domain": ".example.org"},
            {"name": "csrf", "value": "SECRET2", "domain": "api.example.org"},
        ],
    )
    summaries = list_sessions(tmp_path)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.account == "alice"
    assert summary.cookie_count == 2
    assert summary.domains == (".example.org", "api.example.org")
    assert summary.readable is True
    assert summary.path == path
    assert summary.size_bytes > 0
    assert "SECRET" not in repr(summary)  # 摘要里不能出现 cookie 值


def test_unreadable_snapshot_is_kept_and_flagged(tmp_path: Path) -> None:
    """★ 解析不出来的快照照样列出并标 readable=False，不隐藏。"""
    broken = tmp_path / SESSIONS_DIRNAME / f"broken{STORAGE_STATE_SUFFIX}"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{not json", encoding="utf-8")

    summaries = list_sessions(tmp_path)

    assert len(summaries) == 1
    assert summaries[0].readable is False
    assert summaries[0].cookie_count == 0
    assert summaries[0].domains == ()


def test_snapshot_without_cookies_key_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / SESSIONS_DIRNAME / f"origins-only{STORAGE_STATE_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"origins": []}), encoding="utf-8")

    summary = summarize_session(path)

    assert summary.readable is False
    assert summary.cookie_count == 0


def test_list_sessions_sorted_by_recency(tmp_path: Path) -> None:
    import os
    import time

    older = _write(tmp_path, "older-aaaa1111", [])
    time.sleep(0.01)
    newer = _write(tmp_path, "newer-bbbb2222", [])
    os.utime(older, (1_600_000_000, 1_600_000_000))

    names = [item.name for item in list_sessions(tmp_path)]

    assert names == ["newer-bbbb2222", "older-aaaa1111"]
    assert newer.name.startswith("newer")


def test_list_sessions_ignores_non_snapshot_files(tmp_path: Path) -> None:
    _write(tmp_path, "keep-me-cccc", [])
    (tmp_path / SESSIONS_DIRNAME / "default.cookies").write_text("x", encoding="utf-8")
    (tmp_path / SESSIONS_DIRNAME / "notes.txt").write_text("x", encoding="utf-8")

    assert [item.name for item in list_sessions(tmp_path)] == ["keep-me-cccc"]


def test_remove_session_deletes_only_the_snapshot(tmp_path: Path) -> None:
    target = _write(tmp_path, session_name("default|"), [])
    sibling = _write(tmp_path, session_name("alice|"), [])

    remove_session(target, workspace=tmp_path)

    assert not target.exists()
    assert sibling.is_file()


def test_remove_session_refuses_paths_outside_the_sessions_dir(tmp_path: Path) -> None:
    outside = tmp_path / f"outside{STORAGE_STATE_SUFFIX}"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError):
        remove_session(outside, workspace=tmp_path)
    assert outside.is_file()


def test_remove_session_refuses_nested_and_wrong_suffix(tmp_path: Path) -> None:
    nested_dir = tmp_path / SESSIONS_DIRNAME / "nested"
    nested_dir.mkdir(parents=True)
    nested = nested_dir / f"deep{STORAGE_STATE_SUFFIX}"
    nested.write_text("{}", encoding="utf-8")
    wrong_suffix = tmp_path / SESSIONS_DIRNAME / "plain.json"
    wrong_suffix.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError):
        remove_session(nested, workspace=tmp_path)
    with pytest.raises(ValueError):
        remove_session(wrong_suffix, workspace=tmp_path)
    assert nested.is_file() and wrong_suffix.is_file()


def test_remove_session_is_idempotent_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / SESSIONS_DIRNAME / f"gone{STORAGE_STATE_SUFFIX}"
    missing.parent.mkdir(parents=True, exist_ok=True)
    remove_session(missing, workspace=tmp_path)  # 不抛错


def test_summarize_account_falls_back_to_name_without_digest(tmp_path: Path) -> None:
    path = tmp_path / SESSIONS_DIRNAME / f"plainname{STORAGE_STATE_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": []}), encoding="utf-8")

    assert summarize_session(path).account == "plainname"


def test_storage_state_path_lives_under_workspace_sessions(tmp_path: Path) -> None:
    path = storage_state_path(tmp_path, "default|")
    assert path.parent == tmp_path / SESSIONS_DIRNAME
    assert path.name.endswith(STORAGE_STATE_SUFFIX)
