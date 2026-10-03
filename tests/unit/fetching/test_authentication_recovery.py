from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawler.core.auth_policy import validate_auth_check
from omnicrawler.core.config import load_config
from omnicrawler.core.errors import SessionExpiredError
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.core.secrets_store import SecretsStore
from omnicrawler.fetching import session_crypto
from omnicrawler.fetching.authentication import check_authentication, session_scope
from omnicrawler.fetching.session_state import context_key_for_request, require_session_state_path
from omnicrawler.pipeline import Pipeline
from omnicrawler.runtime.recovery import RecoveryCenter
from omnicrawler.state import StateStore


def _config(tmp_path: Path, check: str = "{}"):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: auth, workspace: work}\n"
                    f"source: {{kind: static_html, seeds: [https://example.test/], auth_check: {check}}}\n"
                    "http: {delay_seconds: 0}\n"
                    "session: {persist_cookies: true, name: primary}\n", encoding="utf-8")
    return load_config(path)


@pytest.mark.parametrize("status", [401, 403])
def test_status_alone_never_infers_authentication_failure(tmp_path, status):
    config = _config(tmp_path)
    request = CrawlRequest("https://example.test/")
    result = FetchResult(request, request.url, status, {"content-type": "text/html"}, b"Denied", 0)
    check_authentication(config, result)


def test_explicit_check_requires_all_declared_conditions(tmp_path):
    config = _config(tmp_path, "{status_codes: [403], selector: 'form.login'}")
    request = CrawlRequest("https://example.test/")
    response = FetchResult(request, request.url, 403, {"content-type": "text/html"}, b"<p>Permission denied</p>", 0)
    check_authentication(config, response)
    response.body = b'<form class="login"></form>'
    with pytest.raises(SessionExpiredError) as error:
        check_authentication(config, response)
    assert error.value.scope == session_scope(config, request)
    assert not error.value.retryable


def test_retry_filters_reason_scope_status_and_previous_generation(tmp_path):
    config = _config(tmp_path)
    scope = session_scope(config, CrawlRequest("https://example.test/"))
    generation = hashlib.sha256(b"new login").hexdigest()
    other_scope = session_scope(config, CrawlRequest("https://example.test/", meta={"account": "other"}))
    with StateStore(tmp_path / "state.sqlite3") as state:
        requests = [CrawlRequest(f"https://example.test/{name}") for name in ("auth", "other", "network", "blocked", "done")]
        for request in requests:
            state.enqueue(request)
        for request in state.claim(5):
            if request.url.endswith("other"):
                state.mark_failed(request, SessionExpiredError(other_scope), 3, retryable=False)
            elif request.url.endswith("network"):
                state.mark_failed(request, TimeoutError("network"), 3, retryable=False)
            else:
                state.mark_failed(request, SessionExpiredError(scope), 3, retryable=False)
        state.mark_done(requests[3].fingerprint, status="blocked")
        state.mark_done(requests[4].fingerprint)
        fingerprints = [request.fingerprint for request in requests]
        assert state.authentication_failures(scope) == [requests[0].fingerprint]
        assert state.retry_authentication(fingerprints, scope=scope, generation=generation) == 1
        restored = state.claim(1)[0]
        state.mark_failed(restored, SessionExpiredError(scope), 3, retryable=False)
        assert state.retry_authentication(fingerprints, scope=scope, generation=generation) == 0
        assert state.retry_authentication(fingerprints, scope=scope, generation=hashlib.sha256(b"another login").hexdigest()) == 1
        statuses = {row["url"].rsplit("/", 1)[-1]: row["status"] for row in state.rows("SELECT url,status FROM frontier")}
        assert statuses == {"auth": "pending", "other": "failed", "network": "failed", "blocked": "blocked", "done": "done"}


def test_production_pipeline_persists_explicit_failure_and_resumes_once(tmp_path):
    config = _config(tmp_path, "{status_codes: [401]}")
    request = CrawlRequest("https://example.test/")
    scope = session_scope(config, request)
    generation = hashlib.sha256(b"accepted login").hexdigest()
    for index in range(2):
        with Pipeline(config) as pipeline:
            pipeline.robots.allowed = lambda _url: True
            pipeline.scope = SimpleNamespace(allowed=lambda *_args: (True, ""))
            pipeline._thread_fetcher = lambda _name: SimpleNamespace(fetch=lambda request: FetchResult(
                request, request.url, 401, {"content-type": "text/html"}, b"Login required", 0.1,
            ))
            pipeline.run(resume=index > 0)
            failures = pipeline.state.authentication_failures(scope)
            assert failures == [request.fingerprint]
            assert pipeline.state.retry_authentication(failures, scope=scope, generation=generation) == (1 if index == 0 else 0)


@pytest.mark.parametrize("check", ["bad", {"status_codes": [500]}, {"selector": ""}, {"redirect_paths": []}, {"unknown": True}])
def test_invalid_auth_checks_rejected(check):
    assert validate_auth_check(check)


def test_saved_login_retries_only_cookie_covered_domain_and_bound_account(tmp_path, monkeypatch):
    config = _config(tmp_path)
    passwords = {}
    keyring = SimpleNamespace(get_password=lambda service, account: passwords.get((service, account)),
                              set_password=lambda service, account, value: passwords.__setitem__((service, account), value))
    store = SecretsStore(tmp_path / "test-secrets.bin", keyring_api=keyring)
    monkeypatch.setattr(session_crypto, "SecretsStore", lambda: store)
    request = CrawlRequest("https://example.test/")
    other = CrawlRequest("https://other.test/")
    snapshot = require_session_state_path(config, context_key_for_request(config, request))
    session_crypto.save_storage_state({"cookies": [
        {"name": "session", "value": "test-value", "domain": "example.test", "path": "/", "expires": -1},
    ]}, snapshot, store=store)
    with StateStore(config.workspace / "state.sqlite3") as state:
        for item in (request, other):
            state.enqueue(item)
            state.mark_failed(item, SessionExpiredError(session_scope(config, item)), 3, retryable=False)
    result = RecoveryCenter(config).retry_after_login(snapshot, hosts=("example.test", "other.test"))
    assert result["retried"] == 1
    assert RecoveryCenter(config).retry_after_login(snapshot, hosts=("example.test",))["retried"] == 0
    different_account = require_session_state_path(config, context_key_for_request(config, CrawlRequest(request.url, meta={"account": "another"})))
    different_account.write_bytes(snapshot.read_bytes())
    with pytest.raises(ValueError, match="账号或代理"):
        RecoveryCenter(config).retry_after_login(different_account, hosts=("example.test",))
    with StateStore(config.workspace / "state.sqlite3") as state:
        assert state.authentication_failures(session_scope(config, other)) == [other.fingerprint]
