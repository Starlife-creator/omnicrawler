"""P2-2：浏览器 Profile 注册表。

借鉴 Botasaurus Profile 持久化的底层思想：
  * 按"域名（经 site_aliases 归并）+ 账户"维度分配独立 profile 目录；
  * 目录可跨 run 复用，减少重复登录；
  * 带生命周期：按时间 LRU 过期 + 显式清理 API。

严格合规边界（与 Botasaurus 的反检测实现严格区分）：
  * 仅提供目录分配/生命周期管理，不含任何请求侧伪装、指纹修改或反反爬；
  * 所有 profile 存放于 workspace/browser_profiles/，权限 0o700；
  * 跨 profile 路径严格隔离，A 域 profile 绝不泄露到 B 域。

与 PlaywrightPool 现有 storage_state.json 的区别：
  * storage_state.json 只是 cookies/localStorage 的快照（Playwright 专有）
  * profile_dir 是完整 Chromium user_data_dir（含 http cache、service worker、indexedDB 等）
    → 可被 Selenium / Playwright 共用
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 安全导入 site_aliases：若未初始化则跳过域名归并（退化行为）
try:
    from ..core.site_aliases import SiteAliasRegistry, normalize_host
except Exception:  # noqa: BLE001
    def normalize_host(host: str) -> str:  # type: ignore[misc]
        return (host or "").strip().rstrip(".").casefold()

    SiteAliasRegistry = None  # type: ignore[assignment,misc]

DEFAULT_MAX_PROFILES = 32
DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 天未访问则清理
MANIFEST_NAME = "_omnicrawler_profile.json"

__all__ = [
    "DEFAULT_MAX_PROFILES",
    "DEFAULT_TTL_SECONDS",
    "BrowserProfile",
    "ProfileRegistry",
]


def _safe_name(fragment: str) -> str:
    """把任意字符串转成文件系统安全的名字（保留少量合法字符，其余哈希）。"""
    if not fragment:
        return "default"
    allowed = []
    for ch in fragment:
        if ch.isalnum() or ch in "-_":
            allowed.append(ch)
        else:
            allowed.append("_")
    short = "".join(allowed).strip("_")[:64] or "_"
    digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()[:8]
    return f"{short}-{digest}"


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    """单个 profile 描述（仅值对象，不持有锁）。"""

    scope: str            # 规范化的 scope key（host|account）
    root: Path            # user_data_dir 路径
    manifest_path: Path   # manifest 路径（含 last_accessed 等元数据）

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    def ensure(self) -> Path:
        """确保目录存在并刷新 last_accessed。返回 root。"""
        self.root.mkdir(parents=True, exist_ok=True)
        # 目录权限：仅 owner 可读可写（类 Unix；Windows 忽略）
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self._touch_manifest()
        return self.root

    def _touch_manifest(self) -> None:
        """写入/刷新 manifest（last_accessed / scope / created_at）。"""
        now = time.time()
        data: dict[str, Any] = {"scope": self.scope, "last_accessed": now}
        try:
            if self.manifest_path.is_file():
                try:
                    prior = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                    if isinstance(prior, dict):
                        data["created_at"] = prior.get("created_at", now)
                except (OSError, json.JSONDecodeError):
                    data["created_at"] = now
            else:
                data["created_at"] = now
            tmp = self.manifest_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            tmp.replace(self.manifest_path)
            try:
                self.manifest_path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            # 写 manifest 失败不影响主流程（profile 目录本身存在即可用）
            pass

    def delete(self) -> None:
        """安全删除整个 profile 目录（先写标记，避免并行写入造成半截）。"""
        if not self.root.exists():
            return
        marker = self.root / "_omnicrawler_deleting.flag"
        try:
            marker.write_text(str(int(time.time())), encoding="utf-8")
        except OSError:
            pass
        # 三次重试：可能有 Chromium 残留句柄未释放
        last_err: Exception | None = None
        for _ in range(3):
            try:
                shutil.rmtree(self.root, ignore_errors=False)
                return
            except OSError as exc:
                last_err = exc
                time.sleep(0.2)
        # 最后兜底：ignore_errors=True，至少保证目录"物理删除但可能留残留文件"
        try:
            shutil.rmtree(self.root, ignore_errors=True)
        except OSError as exc2:  # pragma: no cover - 极端场景
            last_err = exc2
        # 无法删除时静默：下次 GC 再尝试
        _ = last_err

    def last_accessed(self) -> float:
        """从 manifest 读取上次访问时间；缺失返回 0。"""
        if not self.manifest_path.is_file():
            return 0.0
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return float(data.get("last_accessed", 0.0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return 0.0


class ProfileRegistry:
    """浏览器 Profile 注册表 — 按 scope 分配、跟踪、清理。

    线程安全；scope 一般是 ``resolve(host) + "|" + account``，由 :meth:`acquire` 内部构造。
    """

    __slots__ = ("_root", "_max_profiles", "_ttl_seconds", "_lock")

    def __init__(
        self,
        root: Path,
        *,
        max_profiles: int = DEFAULT_MAX_PROFILES,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        self._root = root
        self._max_profiles = max(1, int(max_profiles))
        self._ttl_seconds = max(0, int(ttl_seconds))
        self._lock = threading.RLock()

    # ── 公共 API ──────────────────────────────────────────
    @property
    def root(self) -> Path:
        return self._root

    def scope_for(self, host: str, *, account: str = "", environment: str | None = None) -> str:
        """构造规范化 scope 键：``归并域名|账户|环境``。"""
        h = normalize_host(host)
        if SiteAliasRegistry is not None:
            try:
                h = SiteAliasRegistry.default().resolve(h, environment=environment) or h
            except Exception:  # noqa: BLE001
                pass
        acct = (account or "default").strip() or "default"
        env_key = (environment or "").strip()
        if env_key:
            return f"{h}|{acct}|{env_key}"
        return f"{h}|{acct}"

    def acquire(
        self,
        host: str,
        *,
        account: str = "",
        environment: str | None = None,
    ) -> BrowserProfile:
        """获取（或分配）一个 profile 目录；刷新 last_accessed。"""
        scope = self.scope_for(host, account=account, environment=environment)
        with self._lock:
            root = self._root / _safe_name(scope)
            profile = BrowserProfile(scope=scope, root=root, manifest_path=root / MANIFEST_NAME)
            profile.ensure()
            # 每 20 次 acquire 触发一次懒清理（不阻塞关键路径：用 LRU 思路先清最久未用直到 ≤max）
            self._maybe_gc_locked()
            return profile

    def lookup(self, host: str, *, account: str = "", environment: str | None = None) -> BrowserProfile | None:
        """仅查询，不创建。"""
        scope = self.scope_for(host, account=account, environment=environment)
        with self._lock:
            root = self._root / _safe_name(scope)
            if not root.is_dir():
                return None
            return BrowserProfile(scope=scope, root=root, manifest_path=root / MANIFEST_NAME)

    def list_all(self) -> list[BrowserProfile]:
        """列出当前 root 下所有合法 profile（含子目录含 manifest 的）。"""
        out: list[BrowserProfile] = []
        with self._lock:
            for child in sorted(self._root.iterdir()):
                if not child.is_dir():
                    continue
                manifest = child / MANIFEST_NAME
                if not manifest.is_file():
                    continue
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    scope = data.get("scope", "") if isinstance(data, dict) else ""
                except (OSError, json.JSONDecodeError):
                    scope = ""
                out.append(BrowserProfile(scope=scope or child.name, root=child, manifest_path=manifest))
        return out

    def purge_expired(self, *, now: float | None = None) -> int:
        """清理过期（未访问超过 TTL）或数量超限的最久未用 profile。返回删除数量。"""
        count = 0
        with self._lock:
            count += self._purge_expired_locked(now=now)
            count += self._purge_over_limit_locked()
        return count

    def clear(self) -> int:
        """删除全部 profile。返回删除数量。"""
        count = 0
        with self._lock:
            for p in self.list_all():
                p.delete()
                count += 1
        return count

    # ── 内部 ──────────────────────────────────────────────
    def _maybe_gc_locked(self) -> None:
        # 每 1/N 次触发；用 manifest 路径自身存在性 + mtime 近似判断，避免重 IO
        # 这里只对数量超限时做清最久未用
        try:
            existing = [p for p in self._root.iterdir() if p.is_dir()]
        except OSError:
            return
        if len(existing) > self._max_profiles:
            self._purge_over_limit_locked()

    def _purge_expired_locked(self, *, now: float | None) -> int:
        if self._ttl_seconds <= 0:
            return 0
        now = time.time() if now is None else float(now)
        count = 0
        for profile in self.list_all():
            last = profile.last_accessed()
            if last <= 0:
                # 无 manifest 访问时间：用目录 stat 兜底
                try:
                    last = profile.root.stat().st_mtime
                except OSError:
                    last = now
            if now - last > self._ttl_seconds:
                profile.delete()
                count += 1
        return count

    def _purge_over_limit_locked(self) -> int:
        try:
            profiles = self.list_all()
        except OSError:
            return 0
        if len(profiles) <= self._max_profiles:
            return 0
        # 按 last_accessed 升序排（最久未用先删）
        profiles.sort(key=lambda p: (p.last_accessed(), str(p.root)))
        excess = len(profiles) - self._max_profiles
        count = 0
        for p in profiles[:excess]:
            p.delete()
            count += 1
        return count
