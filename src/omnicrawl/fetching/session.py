from __future__ import annotations

import os
import threading
from http.cookiejar import CookieJar, LWPCookieJar
from pathlib import Path

from ..core.config import AppConfig


class CookieSession:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.jar: CookieJar = LWPCookieJar(str(path)) if path else CookieJar()
        if path and path.is_file():
            try:
                self.jar.load(ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        if not self.path:
            return
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.jar.save(ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass


_SESSIONS: dict[Path, CookieSession] = {}
_LOCK = threading.Lock()


def get_cookie_session(config: AppConfig) -> CookieSession:
    settings = config.section("session")
    if not settings.get("persist_cookies", False):
        return CookieSession(None)
    name = str(settings.get("name", "default")).strip() or "default"
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in name)
    path = (config.workspace / "sessions" / f"{safe_name}.cookies").resolve()
    with _LOCK:
        return _SESSIONS.setdefault(path, CookieSession(path))
