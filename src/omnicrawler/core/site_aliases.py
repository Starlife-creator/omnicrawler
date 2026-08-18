"""P2-5：域名 / 站点别名注册表 + 环境隔离别名。

借鉴 Repo Swap / Domain Swapper 的底层思想（命名映射表 + 环境标签），
但本项目仅在"合规的观测聚合层"使用：把同一业务的多个域名、多环境（dev/staging/prod）
镜像域名归并为 canonical 域名，以便指标、去重、规则复用。

本模块**不做**任何网络层的 DNS 重写或请求拦截，仅提供纯函数式映射。

典型用法：
    reg = SiteAliasRegistry.default()
    reg.add_alias("m.shop.example.com", "shop.example.com")          # 移动站 = 主站
    reg.add_alias("shop-preview.example.com", "shop.example.com",    # 预览 = 生产
                  environments={"preview", "dev"})
    reg.resolve("m.shop.example.com")           # → "shop.example.com"
    reg.resolve("shop-preview.example.com", environment="dev")  # → "shop.example.com"
    reg.resolve("shop-preview.example.com")     # → "shop-preview.example.com"（无环境标签不生效）
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field

DEFAULT_ENVIRONMENT_VAR = "OMNICRAWL_ENVIRONMENT"


def normalize_host(host: str) -> str:
    """域名键归一化：去末尾点、全小写、去前后空白；空串返回 ''。

    与 services/metrics.py、security/policy.py 内 casefold 约定保持一致。
    """
    if not host:
        return ""
    return host.strip().rstrip(".").casefold()


@dataclass
class _AliasEntry:
    """单条别名记录的内部表示。"""

    canonical: str
    #: 生效环境集合；空集合 = 任何环境都生效（全局别名，如 m.example.com → example.com）
    environments: frozenset[str] = field(default_factory=frozenset)


class SiteAliasRegistry:
    """域名别名注册表：别名 → 规范化 canonical，可选带环境维度。

    设计目标（与现有 EntityRegistry 风格对齐）：
    * 循环别名自动检测并抛 ValueError（见 `resolve`）
    * 环境维度互斥："preview 环境的 A → canonicalA" 不影响生产环境解析
    * 线程安全：可在 pipeline worker + GUI 指标面板同时读写
    """

    __slots__ = ("_aliases", "_lock", "_default_environment")

    def __init__(self, *, default_environment: str = "") -> None:
        # key = 归一化后的 alias host；value = _AliasEntry
        self._aliases: dict[str, _AliasEntry] = {}
        self._lock = threading.RLock()
        self._default_environment = default_environment or os.environ.get(
            DEFAULT_ENVIRONMENT_VAR, ""
        ).strip()

    # ── 工厂 ──────────────────────────────────────────────
    @classmethod
    def default(cls) -> SiteAliasRegistry:
        """获取进程级共享实例。

        采用首次访问时 lazy 创建，避免在仅 import core 模块时付出锁成本。
        """
        global _DEFAULT_REGISTRY  # noqa: PLW0603
        if _DEFAULT_REGISTRY is None:
            with _DEFAULT_REGISTRY_LOCK:
                if _DEFAULT_REGISTRY is None:
                    _DEFAULT_REGISTRY = cls()
        return _DEFAULT_REGISTRY

    # ── 配置 ──────────────────────────────────────────────
    @property
    def default_environment(self) -> str:
        return self._default_environment

    def set_default_environment(self, env: str) -> None:
        with self._lock:
            self._default_environment = (env or "").strip()

    def add_alias(
        self,
        alias_host: str,
        canonical_host: str,
        *,
        environments: Iterable[str] | None = None,
    ) -> None:
        """新增一条别名规则。

        Parameters
        ----------
        alias_host:
            输入域名（如 m.example.com、preview.example.com）。
        canonical_host:
            归并后的 canonical 域名；会再走一次 :meth:`resolve` 保证传递性。
        environments:
            可选。若提供，该别名仅在传入的 environment 命中时生效；
            留空表示"所有环境都生效"（如移动站 / 主站这种与环境无关的映射）。
        """
        a = normalize_host(alias_host)
        c = normalize_host(canonical_host)
        if not a or not c:
            # 空域名：忽略（不抛；保持幂等友好）
            return
        if a == c:
            # 自指：无需存储
            return
        env_set = frozenset(e.strip() for e in (environments or ()) if e and e.strip())
        with self._lock:
            # 先把 canonical 本身再 resolve 一次，保证 alias 链归并到最终 canonical
            final_canonical = self._resolve_locked(c, self._default_environment)
            existing = self._aliases.get(a)
            if existing is not None:
                # 同 alias 多条规则：合并环境集合；若新增规则为全局（env_set 空）→ 升级为全局
                if not env_set or not existing.environments:
                    merged_env: frozenset[str] = frozenset()
                else:
                    merged_env = existing.environments | env_set
                self._aliases[a] = _AliasEntry(final_canonical, merged_env)
            else:
                self._aliases[a] = _AliasEntry(final_canonical, env_set)
            # 校验无循环（resolve 路径走到自身必 throw）
            self._resolve_locked(a, self._default_environment)

    def clear(self) -> None:
        with self._lock:
            self._aliases.clear()

    # ── 查询 ──────────────────────────────────────────────
    def resolve(self, host: str, *, environment: str | None = None) -> str:
        """把任意 host 归并为 canonical；找不到则原样返回（归一化后）。

        Raises ``ValueError`` 当存在别名循环。
        """
        with self._lock:
            env = self._pick_env(environment)
            return self._resolve_locked(host, env)

    def has_alias(self, host: str, *, environment: str | None = None) -> bool:
        """host 是否有生效的别名（即 resolve 结果 != 自身）。"""
        h = normalize_host(host)
        if not h:
            return False
        with self._lock:
            return self._resolve_locked(h, self._pick_env(environment)) != h

    def aliases_for(self, canonical_host: str, *, environment: str | None = None) -> list[str]:
        """反向查询：哪些 alias 归并到了给定的 canonical。

        主要供规则复用场景（"同站点规则可共享"）。返回归一化后的 alias 列表。
        """
        target = normalize_host(canonical_host)
        if not target:
            return []
        out: list[str] = []
        env = self._pick_env(environment)
        with self._lock:
            for alias, entry in self._aliases.items():
                if self._env_matches(entry.environments, env):
                    # 需要走 resolve 确认最终落点确实等于 target（处理传递性）
                    if self._resolve_locked(alias, env) == target:
                        out.append(alias)
        out.sort()
        return out

    # ── 内部 ──────────────────────────────────────────────
    @staticmethod
    def _env_matches(rule_envs: frozenset[str], current_env: str) -> bool:
        # 空集合 = 全局生效
        if not rule_envs:
            return True
        if not current_env:
            return False
        return current_env in rule_envs

    def _pick_env(self, environment: str | None) -> str:
        if environment is None:
            return self._default_environment
        return environment.strip()

    def _resolve_locked(self, host: str, env: str) -> str:
        current = normalize_host(host)
        if not current:
            return ""
        seen: set[str] = set()
        while True:
            entry = self._aliases.get(current)
            if entry is None:
                return current
            if not self._env_matches(entry.environments, env):
                return current
            nxt = entry.canonical
            if nxt == current:
                return current
            if current in seen:
                raise ValueError(f"域名别名存在循环: {' → '.join(sorted(seen))}")
            seen.add(current)
            current = nxt


# 进程级单例（lazy 初始化）
_DEFAULT_REGISTRY: SiteAliasRegistry | None = None
_DEFAULT_REGISTRY_LOCK = threading.Lock()


__all__ = [
    "DEFAULT_ENVIRONMENT_VAR",
    "SiteAliasRegistry",
    "normalize_host",
]
