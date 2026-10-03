from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.fetching.profile_registry import ProfileRegistry
from omnicrawler.fetching.session import get_cookie_session
from omnicrawler.fetching.session_bridge import bridge_from_storage_state_file
from omnicrawler.fetching.session_lease import session_lease
from omnicrawler.fetching.session_state import context_key, require_session_state_path
from omnicrawler.pipeline import Pipeline
from omnicrawler.runtime.recovery import RecoveryCenter


def _config(tmp_path: Path) -> AppConfig:
    from omnicrawler.core.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text("project: {name: sessions, workspace: work}\n"
                    "source: {kind: static_html, seeds: [https://example.test/]}\n"
                    "session: {persist_cookies: true, name: default}\n", encoding="utf-8")
    return load_config(path)


def test_active_pipeline_blocks_session_replacement_and_reset(tmp_path):
    config = _config(tmp_path)
    with Pipeline(config):
        with pytest.raises(TimeoutError, match="资源关闭"):
            RecoveryCenter(config).reset_login()
        with pytest.raises(TimeoutError, match="资源关闭"):
            bridge_from_storage_state_file(config, tmp_path / "snapshot", hosts=["example.test"])
    assert RecoveryCenter(config).reset_login()["moved"] == 0


def test_reset_evicts_cached_jar_and_prevents_old_reference_from_restoring_file(tmp_path):
    config = _config(tmp_path)
    old = get_cookie_session(config)
    assert old.path is not None
    old.path.parent.mkdir(parents=True)
    old.path.write_bytes(b"retained encrypted session placeholder")
    result = RecoveryCenter(config).reset_login()
    assert result["moved"] == 1
    old.save()
    replacement = get_cookie_session(config)
    assert replacement is not old
    assert old.path is None
    assert replacement.path is not None and not replacement.path.exists()


def test_pipeline_close_waits_for_inflight_work_before_releasing_session_lease(tmp_path):
    config = _config(tmp_path)
    pipeline = Pipeline(config)
    started = threading.Event()
    finish = threading.Event()
    close_done = threading.Event()
    seen = []

    def inflight():
        started.set()
        assert finish.wait(5)
        seen.append("task_finished")

    pipeline._all_fetchers.append(SimpleNamespace(close=lambda: seen.append("client_closed")))
    pipeline._get_executor(1).submit(inflight)
    assert started.wait(2)

    def close():
        pipeline.close()
        close_done.set()

    thread = threading.Thread(target=close)
    thread.start()
    try:
        assert not close_done.wait(0.1)
        with pytest.raises(TimeoutError):
            with session_lease(config.workspace):
                pass
    finally:
        finish.set()
        thread.join(5)
    assert close_done.is_set()
    assert seen == ["task_finished", "client_closed"]
    with session_lease(config.workspace):
        pass
    pipeline.close()


def test_logout_clears_current_account_resources_and_preserves_other_account(tmp_path):
    config = _config(tmp_path)
    http = get_cookie_session(config)
    assert http.path is not None
    snapshot = require_session_state_path(config, context_key(account="default", proxy=""))
    other_snapshot = require_session_state_path(config, context_key(account="other", proxy=""))
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    for path in (http.path, snapshot, other_snapshot):
        path.write_bytes(b"test-only session")
    profiles = ProfileRegistry(config.workspace / "browser_profiles")
    current_profile = profiles.acquire("example.test", account="default")
    other_profile = profiles.acquire("example.test", account="other")
    result = RecoveryCenter(config).logout_current_session()
    assert result["moved"] == 3
    assert not snapshot.exists()
    assert http.path is None
    assert not current_profile.root.exists()
    assert other_snapshot.exists() and other_profile.root.exists()
    quarantine = Path(result["quarantine"])
    assert (quarantine / "sessions" / snapshot.name).is_file()
    assert not (config.workspace / "sessions" / "default.cookies").exists()
    http.save()
    assert not (config.workspace / "sessions" / "default.cookies").exists()


def test_logout_restores_files_when_a_move_fails(tmp_path, monkeypatch):
    config = _config(tmp_path)
    http = get_cookie_session(config)
    assert http.path is not None
    snapshot = require_session_state_path(config, context_key(account="default", proxy=""))
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"original snapshot")
    http.path.write_bytes(b"original cookie file")
    cookie_path = http.path
    original_replace = Path.replace

    def fail_cookie_move(path, destination):
        if path == cookie_path:
            raise OSError("injected move failure")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_cookie_move)
    with pytest.raises(OSError, match="injected"):
        RecoveryCenter(config).logout_current_session()
    assert snapshot.read_bytes() == b"original snapshot"
    assert cookie_path.read_bytes() == b"original cookie file"
