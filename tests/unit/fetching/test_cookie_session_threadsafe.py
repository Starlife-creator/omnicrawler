"""S2.5.8：CookieSession jar 线程安全（并发读写无竞态）。"""

from __future__ import annotations

import threading
from http.cookiejar import Cookie
from pathlib import Path

from omnicrawl.fetching.session import CookieSession, get_cookie_session


def test_concurrent_jar_reads_and_writes_no_loss(tmp_path: Path) -> None:
    session = CookieSession(None)
    threads: list[threading.Thread] = []
    errors: list[Exception] = []

    def _write(index: int) -> None:
        try:
            for offset in range(50):
                cookie = Cookie(
                    version=0, name=f"key_{index}", value=str(offset),
                    port=None, port_specified=False,
                    domain="example.org", domain_specified=True, domain_initial_dot=False,
                    path="/", path_specified=True,
                    secure=False, expires=None, discard=True, comment=None,
                    comment_url=None, rest={}, rfc2109=False,
                )
                session.jar.set_cookie(cookie)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def _read() -> None:
        try:
            for _ in range(200):
                session.jar._cookies.get("example.org", {})  # noqa: SLF001
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    for index in range(8):
        threads.append(threading.Thread(target=_write, args=(index,)))
    for _ in range(4):
        threads.append(threading.Thread(target=_read))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    jar = session.jar._cookies  # noqa: SLF001
    assert len(jar["example.org"]["/"]) == 8


def test_concurrent_save_does_not_crash(tmp_path: Path) -> None:
    session = CookieSession(None)
    session.path = tmp_path / "sessions" / "default.cookies"
    session.jar.set_cookie(
        Cookie(
            version=0, name="a", value="b",
            port=None, port_specified=False,
            domain="example.org", domain_specified=True, domain_initial_dot=False,
            path="/", path_specified=True,
            secure=False, expires=None, discard=True, comment=None,
            comment_url=None, rest={}, rfc2109=False,
        )
    )
    errors: list[Exception] = []

    def _save() -> None:
        try:
            for _ in range(20):
                session.save()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_save) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors


def test_singleton_session_returns_same_instance(tmp_path: Path) -> None:
    from omnicrawl.core.config import load_config

    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s258, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "session: {persist_cookies: true, name: shared}\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert get_cookie_session(config) is get_cookie_session(config)
