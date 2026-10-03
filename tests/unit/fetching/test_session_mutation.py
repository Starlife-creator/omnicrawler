from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.fetching.session import get_cookie_session
from omnicrawler.fetching.session_bridge import bridge_from_storage_state_file
from omnicrawler.fetching.session_lease import session_lease
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
