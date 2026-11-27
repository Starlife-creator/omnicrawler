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
        # ★ 2026-11-27：记住"上一次成功落盘时的 cookie 集合"。落盘是 AES-GCM 加密 +
        #   两次文件写 + chmod，实测 **1.8 ms/次**；而 `HTTPFetcher.fetch` 每取一页都调
        #   一次 `save()`。600 页的翻页任务里约 1.1 s 纯粹在做"没有任何变化的重写"，
        #   同时还让杀毒/索引服务反复处理同一个文件。
        #   判据是**内容有没有变**，不是"调没调用"——cookie 变了照样落盘。
        self._saved_fingerprint: tuple[tuple[str, str, str, str], ...] | None = None
        # ★ 只有**文件已经存在且成功加载**时才把当前内容记为"已落盘"。
        #
        #   否则全新会话（空 jar + 磁盘上还没有文件）会被判成"内容没变"而跳过写盘，
        #   于是 `save()` 返回 True 却**根本没创建文件** —— 实测由集成测试当场抓出
        #   （`test_cookie_session_memory_persistence_corrupt_load_and_safe_name`）。
        #   语义上 `save()` 的成功包含"会话已持久化"，而"文件不存在"显然不满足它。
        loaded = False
        if path and path.is_file():
            self._load()
            loaded = True
        if loaded:
            self._saved_fingerprint = self._jar_fingerprint()

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

    def _jar_fingerprint(self) -> tuple[tuple[str, str, str, str], ...]:
        """jar 的内容指纹（domain/path/name/value）。实测 30 条 cookie 约 0.012 ms。

        ★ 刻意**不含** expires/discard 之外的元数据：那些字段变化不影响"发什么 cookie"。
          指纹只用来回答一个问题 —— **这次落盘会不会写出和上次相同的字节**。
        """
        try:
            cookies = list(self.jar)
        except Exception:  # noqa: BLE001 - 读 jar 失败时按"内容未知"处理，宁可多写一次
            return ()
        return tuple(
            sorted(
                (str(c.domain), str(c.path), str(c.name), str(c.value))
                for c in cookies
            )
        )

    def save(self) -> bool:
        with self.lock:
            if not self.path:
                return False
            # ★ 2026-11-27：内容没变就不重写。返回 True 表示"会话已持久化"，语义不变。
            fingerprint = self._jar_fingerprint()
            if fingerprint == self._saved_fingerprint:
                return True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            plain = self.path.with_name(self.path.name + ".plain")
            try:
                self.jar.save(str(plain), ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]
                try:
                    blob = SecretsStore().encrypt(plain.read_bytes())
                except Exception as exc:
                    LOGGER.warning("cookie 加密失败，跳过落盘（会话仍在内存中）: %s", exc)
                    return False
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_bytes(blob)
                os.replace(tmp, self.path)
            finally:
                plain.unlink(missing_ok=True)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass  # Windows 无 POSIX 权限语义，由 AES-GCM 兜底
            self._saved_fingerprint = fingerprint
            return True


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
        if path not in _SESSIONS:
            _SESSIONS[path] = CookieSession(path)
        return _SESSIONS[path]


def invalidate_cookie_sessions(workspace: Path) -> int:
    """After execution resources stop, evict jars and disable stale saves.

    A retained old fetcher reference cannot recreate a removed session file.
    The caller must own the workspace session lease before calling this function.
    """
    root = (workspace / "sessions").resolve()
    with _LOCK:
        paths = [path for path in _SESSIONS if path.parent == root]
        for path in paths:
            session = _SESSIONS.pop(path)
            with session.lock:
                session.jar.clear()
                session.path = None
        return len(paths)
