from __future__ import annotations

import io
import logging
import os
import threading
from http.cookiejar import CookieJar, LWPCookieJar
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.secrets_store import FILE_MAGIC, SecretsStore

LOGGER = logging.getLogger(__name__)


class _ThreadSafeCookieJar(LWPCookieJar):
    """S2.5.8：jar 读写统一锁——urllib HTTPCookieProcessor 与 save() 共用同一把锁。

    进程级单例 jar 会被并发请求线程同时 add_cookie_header/extract_cookies，
    未加锁时 dict 读写竞态会丢 cookie 或崩溃。
    """

    def __init__(self, path: str | None = None) -> None:
        super().__init__(path)
        self._jar_lock = threading.RLock()

    def add_cookie_header(self, request: Any) -> None:
        with self._jar_lock:
            super().add_cookie_header(request)

    def extract_cookies(self, response: Any, request: Any) -> None:
        with self._jar_lock:
            super().extract_cookies(response, request)

    def save(self, *args: Any, **kwargs: Any) -> None:
        with self._jar_lock:
            super().save(*args, **kwargs)

    def load(self, *args: Any, **kwargs: Any) -> None:
        with self._jar_lock:
            super().load(*args, **kwargs)

    def _really_load(self, *args: Any, **kwargs: Any) -> None:
        with self._jar_lock:
            super()._really_load(*args, **kwargs)  # type: ignore[misc]


class CookieSession:
    """Cookie 持久化：加密落盘 + 原子写（S2.2.3）。

    文件以 ``FILE_MAGIC`` 开头的为 secrets_store 加密 blob（AES-GCM）；旧版明文
    LWP 格式仍可读（向后兼容）。加载失败显式告警，不再静默 pass。
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.jar: CookieJar = _ThreadSafeCookieJar(str(path)) if path else _ThreadSafeCookieJar()
        if path and path.is_file():
            self._load()

    def _load(self) -> None:
        if not self.path:
            return
        try:
            raw = self.path.read_bytes()
            if raw.startswith(FILE_MAGIC):
                text = SecretsStore().decrypt(raw).decode("utf-8")
                jar = _ThreadSafeCookieJar()
                jar._really_load(  # noqa: SLF001 - CPython 稳定私有接口
                    io.StringIO(text), self.path.name, ignore_discard=True, ignore_expires=True
                )
                self.jar = jar
            else:
                self.jar.load(ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]
        except Exception as exc:
            LOGGER.warning("cookie 文件加载失败，将新建会话: %s (%s)", self.path, exc)

    def save(self) -> None:
        if not self.path:
            return
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            plain = self.path.with_name(self.path.name + ".plain")
            try:
                self.jar.save(str(plain), ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]
                try:
                    blob = SecretsStore().encrypt(plain.read_bytes())
                except Exception as exc:
                    LOGGER.warning("cookie 加密失败，跳过落盘（会话仍在内存中）: %s", exc)
                    return
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_bytes(blob)
                os.replace(tmp, self.path)
            finally:
                plain.unlink(missing_ok=True)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass  # Windows 无 POSIX 权限语义，由 AES-GCM 兜底


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
