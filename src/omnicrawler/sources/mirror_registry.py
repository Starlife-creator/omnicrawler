"""P3-1：Mirror Registry —— 镜像组健康路由。

借鉴 Dev-Sidecar / FastGithub 的「镜像组管理 + 健康探测 + 回退」纯路由思想，
严格合规边界（与反审查/绕过完全无关）：

    * 所有 mirror host 必须是用户显式写入 config 的已批准域名；
    * 默认永不启用；config 中不存在 ``mirrors`` 节时 registry 完全为空，
      fetch 路径零开销（不会对 URL 做任何改写）；
    * 仅支持「同一 canonical 业务下，多个官方镜像/加速镜像」的路由改写
      （例如 PyPI / npm / Debian 官方多镜像、CDN 多边缘节点）。
    * **绝不**支持未白名单域名的自动 mirror 发现、绝不做协议指纹修改。

使用示例（config YAML）::

    mirrors:
      enabled: true
      # 镜像组：key 为 canonical（经 site_aliases 再归并一次）
      groups:
        pypi.org:
          - host: pypi.org
            weight: 1.0
          - host: mirrors.tuna.tsinghua.edu.cn  # 只要该 host 已在 egress 白名单
            weight: 2.0                         # 更优先
          - host: mirrors.aliyun.com
            weight: 1.5
      # 健康探测
      probe_interval_seconds: 60
      probe_timeout_seconds: 5
      # L2 回退：连续 3 次失败 → 临时摘流
      failure_threshold: 3
      success_threshold: 2
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# 延迟导入；site_aliases 不可用时退化为简单 host 比较
try:
    from ..core.site_aliases import SiteAliasRegistry, normalize_host
except Exception:  # noqa: BLE001
    def normalize_host(host: str) -> str:  # type: ignore[misc]
        return (host or "").strip().rstrip(".").casefold()

    SiteAliasRegistry = None  # type: ignore[assignment,misc]

LOGGER = logging.getLogger(__name__)

__all__ = [
    "MirrorEndpoint",
    "MirrorGroup",
    "MirrorRegistry",
    "MirrorConfigError",
    "rewrite_url",
]


class MirrorConfigError(ValueError):
    """B-3 fail-fast：mirrors.groups 配置在加载预检阶段就未通过安全校验。

    区分普通 ValueError，上层 doctor 预检 / CLI 启动器可按错误类型给出明确提示，
    避免用户以为是运行时抓取失败。
    """


# ── B-3：静态安全预检工具 ─────────────────────────────
# 完全沿用 security.policy 中 is_private_target / is_disallowed_address 的判定思路：
# 私网/保留/回环/链路本地/多播/未指定地址。
def _is_private_or_reserved_host(host: str) -> bool:
    h = (host or "").strip("[]").lower()
    if h in {"localhost", "localhost.localdomain"} or h.endswith(".localhost"):
        return True
    try:
        import ipaddress

        addr = ipaddress.ip_address(h)
    except ValueError:
        return False
    return bool(
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


# RFC 3986 合规 host：不允许 scheme / port / path / whitespace / 非 ASCII 字符
_VALID_HOST_RE = None  # 延迟编译，避免 import 期开销


def _is_valid_host(host: str) -> bool:
    global _VALID_HOST_RE
    if _VALID_HOST_RE is None:
        import re
        _VALID_HOST_RE = re.compile(
            r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$|"
            r"^\[(?:[0-9a-f:]+)\]$",
            re.IGNORECASE,
        )
    if not host or len(host) > 253:
        return False
    # 允许的 IPv6 literal（[::1]），host 本身不能含 scheme/port/path/空白/冒号(非IPv6)
    # 纯 IPv6 用方括号包裹，上面正则已包含；非 IPv6 时不能含冒号
    has_ipv6 = host.startswith("[") and host.endswith("]")
    if not has_ipv6 and ":" in host:
        return False
    return bool(_VALID_HOST_RE.match(host))


@dataclass(slots=True)
class MirrorEndpoint:
    """单个镜像节点。健康分 = EWMA（0.0=最坏，1.0=最好）。"""

    host: str
    weight: float = 1.0
    # 运行时状态
    healthy: bool = True
    score: float = 1.0            # EWMA 健康分
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_probe: float = 0.0        # 0 = 从未探测

    def record_success(self, *, alpha: float = 0.3) -> None:
        self.last_probe = time.monotonic()
        self.consecutive_successes += 1
        self.consecutive_failures = 0
        self.score = (1.0 * alpha) + (self.score * (1.0 - alpha))

    def record_failure(self, *, alpha: float = 0.3) -> None:
        self.last_probe = time.monotonic()
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.score = (0.0 * alpha) + (self.score * (1.0 - alpha))

    def update_healthy(self, failure_threshold: int, success_threshold: int) -> None:
        if self.healthy and self.consecutive_failures >= failure_threshold:
            self.healthy = False
            LOGGER.warning("镜像 %s 连续失败 %d 次，标记不健康", self.host, self.consecutive_failures)
        elif (not self.healthy) and self.consecutive_successes >= success_threshold:
            self.healthy = True
            LOGGER.info("镜像 %s 连续成功 %d 次，恢复健康", self.host, self.consecutive_successes)


@dataclass(slots=True)
class MirrorGroup:
    """同一个 canonical host 的多个镜像节点集合。"""

    canonical: str
    endpoints: list[MirrorEndpoint] = field(default_factory=list)

    def pick(self) -> MirrorEndpoint | None:
        """按 (score * weight) 加权随机选一个健康节点。全不健康时回退第一个。"""
        candidates = [e for e in self.endpoints if e.healthy]
        pool = candidates or self.endpoints
        if not pool:
            return None
        weights = [max(0.01, e.score * e.weight) for e in pool]
        # 确定性加权（不需要 random —— 简单取最大即可，避免不必要随机性）
        best_idx = max(range(len(pool)), key=lambda i: weights[i])
        return pool[best_idx]


class MirrorRegistry:
    """镜像注册表：加载 config 中的 groups，负责 rewrite_url + 成功/失败回写。

    Parameters
    ----------
    config: AppConfig（读取 mirrors: 节；缺失时 registry 为空 = 零开销直通）
    """

    __slots__ = (
        "_groups",          # canonical_host → MirrorGroup
        "_lock",
        "_enabled",
        "_failure_threshold",
        "_success_threshold",
        "_probe_interval",
        "_probe_timeout",
        "_allow_private",   # B-3：对应 http.allow_private_network；False 时禁止私网地址作为镜像节点
        "_preflight_dns",   # B-3：启动时做 DNS 可达性预检（默认 False，避免启动延迟）
        "_preflight_timeout",
    )

    def __init__(
        self,
        config: Any,
        *,
        allow_private_network: bool | None = None,
        preflight_dns: bool = False,
        preflight_timeout_seconds: float = 3.0,
    ) -> None:
        """B-3：新增 fail-fast 预检参数。

        Parameters
        ----------
        allow_private_network:
            对应 http.allow_private_network。False 时 mirrors.groups 中任何私网/保留地址
            直接抛出 MirrorConfigError；若为 None（默认）则从 config.section("http") 中
            读取，缺失时默认 False（保守）。
        preflight_dns:
            是否在加载时做 DNS 可达性预检（默认 False）。True 时额外解析所有 endpoint host
            的 A/AAAA 记录，失败立即报错；启动延迟增加 O(N*RTT)，适合线上 pre-flight。
        preflight_timeout_seconds:
            DNS 预检单条 host 超时（默认 3s）。
        """
        self._lock = threading.Lock()
        self._groups: dict[str, MirrorGroup] = {}
        cfg = config.section("mirrors") if hasattr(config, "section") else {}
        http_cfg = config.section("http") if hasattr(config, "section") else {}
        # 显式参数优先，其次读取 config，再其次保守默认 False
        if allow_private_network is None:
            allow_private_network = bool(http_cfg.get("allow_private_network", False))
        self._allow_private = bool(allow_private_network)
        self._preflight_dns = bool(preflight_dns)
        self._preflight_timeout = float(preflight_timeout_seconds)
        self._enabled = bool(cfg.get("enabled", False))
        self._failure_threshold = int(cfg.get("failure_threshold", 3))
        self._success_threshold = int(cfg.get("success_threshold", 2))
        self._probe_interval = float(cfg.get("probe_interval_seconds", 60))
        self._probe_timeout = float(cfg.get("probe_timeout_seconds", 5))
        if self._enabled:
            self._load_groups(cfg.get("groups", {}) or {})

    # ── 加载 + B-3 fail-fast 预检 ────────────────────────
    def _load_groups(self, raw_groups: Any) -> None:
        if not isinstance(raw_groups, dict):
            return
        all_hosts_to_check: list[tuple[str, str]] = []  # (source_label, host_or_ip)
        for key, endpoints in raw_groups.items():
            canonical = normalize_host(str(key))
            if not canonical or not isinstance(endpoints, list):
                continue
            group = MirrorGroup(canonical=canonical)
            all_hosts_to_check.append((f"group[{key}].canonical", canonical))
            for item in endpoints:
                if not isinstance(item, dict):
                    continue
                host = normalize_host(str(item.get("host", "")))
                if not host:
                    continue
                all_hosts_to_check.append((f"group[{key}].endpoints[].host", host))
                try:
                    weight = float(item.get("weight", 1.0))
                except (TypeError, ValueError):
                    weight = 1.0
                group.endpoints.append(MirrorEndpoint(host=host, weight=max(0.01, weight)))
            if group.endpoints:
                if not any(e.host == canonical for e in group.endpoints):
                    group.endpoints.insert(0, MirrorEndpoint(host=canonical, weight=1.0))
                self._groups[canonical] = group
        # ── B-3：静态预检（启动即失败，不等到运行时抓取才暴露） ─────
        self._static_preflight(all_hosts_to_check)
        if self._preflight_dns:
            self._dns_preflight([h for _, h in all_hosts_to_check])
        LOGGER.info(
            "MirrorRegistry 已加载 %d 个镜像组（enabled=%s，fail_thr=%d，ok_thr=%d，"
            "allow_private=%s，preflight_dns=%s）",
            len(self._groups), self._enabled, self._failure_threshold, self._success_threshold,
            self._allow_private, self._preflight_dns,
        )

    # ── B-3 预检实现 ──────────────────────────────────────
    def _static_preflight(self, labeled_hosts: list[tuple[str, str]]) -> None:
        errors: list[str] = []
        for source, host in labeled_hosts:
            if not _is_valid_host(host):
                errors.append(
                    f"{source}={host!r} 不是合法 host（不得含 scheme/端口/路径，"
                    f"应为域名或 IPv4 或 [IPv6] 字面量）"
                )
                continue
            if not self._allow_private and _is_private_or_reserved_host(host):
                errors.append(
                    f"{source}={host!r} 为私网/保留/回环地址，但 http.allow_private_network=false；"
                    f"拒绝加载以防止无意中把抓取流量指回内网。"
                    f"如需内网镜像，请显式将 http.allow_private_network 设置为 true。"
                )
        if errors:
            joined = "\n  - ".join(errors)
            raise MirrorConfigError(
                "mirrors.groups 预检失败（fail-fast），请修正后再启动。\n  - " + joined
            )

    def _dns_preflight(self, hosts: list[str]) -> None:
        # 去重、忽略已被判定为私网 literal 的 IP（它们不会被 DNS 解析，DNS 预检只解析域名）
        import ipaddress
        unique = []
        for h in hosts:
            if h in unique:
                continue
            try:
                ipaddress.ip_address(h.strip("[]"))
                continue  # 纯 IP，跳过 DNS 预检
            except ValueError:
                unique.append(h)
        errors: list[str] = []
        default_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(self._preflight_timeout)
            for host in unique:
                try:
                    socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                except socket.gaierror as exc:
                    errors.append(f"DNS 解析失败 host={host!r}: {exc}")
                except OSError as exc:
                    errors.append(f"host={host!r} 预检异常: {exc}")
        finally:
            socket.setdefaulttimeout(default_timeout)
        if errors:
            joined = "\n  - ".join(errors)
            raise MirrorConfigError(
                "mirrors.groups DNS 预检失败（preflight_dns=true）。\n  - " + joined
            )

    # ── B-3：预检配置快照（供 doctor 预检 / cli inspect） ──
    @property
    def allow_private_network(self) -> bool:
        return self._allow_private

    @property
    def preflight_dns(self) -> bool:
        return self._preflight_dns

    def validation_snapshot(self) -> dict[str, Any]:
        """doctor / inspect 工具：把当前所有组的 host 清单 + 预检结论返回。

        Returns:
            {
                "enabled": bool,
                "allow_private_network": bool,
                "preflight_dns": bool,
                "groups": {
                    canonical: [
                        {"host": str, "valid_host": bool, "is_private": bool, "ok": bool}
                    ]
                },
                "all_ok": bool,
            }
        """
        groups: dict[str, list[dict[str, Any]]] = {}
        all_ok = True
        for canonical, group in self._groups.items():
            rows: list[dict[str, Any]] = []
            for ep in group.endpoints:
                valid = _is_valid_host(ep.host)
                private = _is_private_or_reserved_host(ep.host)
                ok = valid and (self._allow_private or not private)
                if not ok:
                    all_ok = False
                rows.append({
                    "host": ep.host,
                    "weight": ep.weight,
                    "valid_host": valid,
                    "is_private": private,
                    "ok": ok,
                })
            groups[canonical] = rows
        return {
            "enabled": self._enabled,
            "allow_private_network": self._allow_private,
            "preflight_dns": self._preflight_dns,
            "groups": groups,
            "all_ok": all_ok,
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def group_count(self) -> int:
        return len(self._groups)

    # ── 公开 API ─────────────────────────────────────────
    def resolve_host(self, host: str, *, environment: str | None = None) -> tuple[str | None, str | None]:
        """返回 (canonical_or_None, picked_host_or_None)。

        canonical=None 表示「该 host 未进入任何镜像组」：调用方必须跳过 mirror 成功/失败回写，
        避免把非镜像组的 host 当作健康端点记录。
        """
        if not self._enabled or not self._groups:
            return (None, None)
        canonical = self._canonicalize(host, environment=environment)
        with self._lock:
            group = self._groups.get(canonical)
            if group is None:
                return (None, None)
            picked = group.pick()
            return (canonical, (picked.host if picked is not None else None))

    def rewrite_url(self, url: str, *, environment: str | None = None) -> tuple[str, str | None]:
        """透明改写 URL 为被选中的 mirror。返回 (rewritten_url | original, canonical_or_None)。

        canonical=None 表示未进入镜像组（调用方无需处理 success/failure 回写）。
        """
        if not self._enabled or not self._groups:
            return (url, None)
        parts = urlsplit(url)
        original_host = parts.hostname or ""
        canonical, picked = self.resolve_host(original_host, environment=environment)
        if canonical is None:
            return (url, None)
        if picked is None or picked == normalize_host(original_host):
            return (url, canonical)
        # 仅替换 netloc 中的 host 段；保留端口、userinfo、scheme、path、query、fragment
        if parts.port is None:
            new_netloc = picked
        else:
            new_netloc = f"{picked}:{parts.port}"
        new_parts = (parts.scheme, new_netloc, parts.path, parts.query, parts.fragment)
        rewritten = urlunsplit(new_parts)
        LOGGER.debug("Mirror 路由 %s -> %s (canonical=%s)", url, rewritten, canonical)
        return (rewritten, canonical)

    # ── 成功/失败回写（配合 fetch 钩子更新健康分） ─────
    def record_success(self, canonical: str, picked_host: str) -> None:
        self._record(canonical, picked_host, success=True)

    def record_failure(self, canonical: str, picked_host: str) -> None:
        self._record(canonical, picked_host, success=False)

    def _record(self, canonical: str, picked_host: str, *, success: bool) -> None:
        if not self._enabled or not canonical:
            return
        norm_picked = normalize_host(picked_host)
        with self._lock:
            group = self._groups.get(canonical)
            if group is None:
                return
            for ep in group.endpoints:
                if ep.host == norm_picked:
                    if success:
                        ep.record_success()
                    else:
                        ep.record_failure()
                    ep.update_healthy(self._failure_threshold, self._success_threshold)
                    return

    def endpoint_status(self, canonical: str) -> list[dict[str, Any]] | None:
        """用于调试 / metrics：返回某组全部 endpoint 的健康快照。"""
        with self._lock:
            group = self._groups.get(canonical)
            if group is None:
                return None
            return [
                {
                    "host": e.host,
                    "weight": e.weight,
                    "healthy": e.healthy,
                    "score": round(e.score, 4),
                    "consecutive_failures": e.consecutive_failures,
                    "consecutive_successes": e.consecutive_successes,
                    "last_probe_seconds_ago": (
                        round(time.monotonic() - e.last_probe, 1) if e.last_probe > 0 else None
                    ),
                }
                for e in group.endpoints
            ]

    # ── 辅助 ─────────────────────────────────────────────
    def _canonicalize(self, host: str, *, environment: str | None) -> str:
        h = normalize_host(host)
        if not h:
            return ""
        if SiteAliasRegistry is not None:
            try:
                resolved = SiteAliasRegistry.default().resolve(h, environment=environment)
                if resolved:
                    return resolved
            except Exception:  # noqa: BLE001
                pass
        return h


# ── 顶层便捷函数（给 fetching/http_client 做一次性桥接） ─────
def rewrite_url(registry: MirrorRegistry | None, url: str) -> tuple[str, str | None]:
    """当 registry 为 None 或未启用时，直通。"""
    if registry is None or not registry.enabled:
        return (url, None)
    return registry.rewrite_url(url)
