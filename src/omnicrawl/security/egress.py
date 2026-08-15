from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
import urllib.parse
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ..core.config import AppConfig
from ..core.errors import CredentialScopeError, EgressBudgetExceededError, EgressDisabledError
from ..core.utils import utcnow
from .policy import NetworkTargetPolicy

_SENSITIVE_HEADER = re.compile(
    r"^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|x-auth-token)$",
    re.IGNORECASE,
)
_SENSITIVE_QUERY = re.compile(
    r"(token|key|secret|password|passwd|signature|credential|auth|session|cookie)",
    re.IGNORECASE,
)
LOGGER = logging.getLogger(__name__)


def _domain_matches(host: str, allowed: tuple[str, ...]) -> bool:
    host = host.casefold().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in allowed)


def _normalise_domains(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(dict.fromkeys(str(value).casefold().strip().lstrip(".") for value in values if str(value).strip()))


def redact_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    safe_query = urllib.parse.urlencode(
        [(key, "***" if _SENSITIVE_QUERY.search(key) else value) for key, value in query]
    )
    # ``netloc`` can itself contain credentials (``https://user:secret@host``).
    # Keep the destination useful for incident analysis while never serialising
    # userinfo to the append-only audit trail.
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, safe_query, ""))


@dataclass(frozen=True, slots=True)
class NetworkCapability:
    token: str = field(repr=False)
    subject: str
    domains: tuple[str, ...]
    purposes: tuple[str, ...]
    maximum_requests: int = 0


@dataclass(frozen=True, slots=True)
class EgressSnapshot:
    requests: int
    response_bytes: int
    cost: float
    active: int
    elapsed_seconds: float


class EgressBroker:
    """Single policy, budget, kill-switch and audit boundary for task networking."""

    _global_disabled = threading.Event()

    def __init__(self, config: AppConfig, *, policy: NetworkTargetPolicy | None = None) -> None:
        """Initialize the broker with policy, budgets, circuit breakers, and audit.

        Args:
            config: Fully-resolved application configuration.
            policy: Optional override network target policy.
        """
        self.config = config
        self.policy = policy or NetworkTargetPolicy(config)
        settings = config.section("egress")
        self.enabled = bool(settings.get("enabled", True))
        self.allowed_schemes = tuple(
            str(item).casefold() for item in settings.get("allowed_schemes", ["http", "https", "ws", "wss"])
        )
        self.allowed_ports = tuple(int(item) for item in settings.get("allowed_ports", []))
        self.allowed_domains = _normalise_domains(settings.get("allowed_domains", []))
        configured_credentials = _normalise_domains(settings.get("credential_domains", []))
        seed_domains = tuple(
            urllib.parse.urlsplit(str(seed)).hostname or ""
            for seed in config.section("source").get("seeds", [])
        )
        declared_service_domains: list[str] = []
        login = config.section("source").get("login", {})
        if isinstance(login, dict):
            declared_service_domains.append(urllib.parse.urlsplit(str(login.get("url", ""))).hostname or "")
        providers = config.section("ai").get("providers", {})
        if isinstance(providers, dict):
            for provider in providers.values():
                if isinstance(provider, dict):
                    declared_service_domains.append(
                        urllib.parse.urlsplit(str(provider.get("base_url", ""))).hostname or ""
                    )
        objects = config.section("storage").get("objects", {})
        if isinstance(objects, dict):
            declared_service_domains.append(
                urllib.parse.urlsplit(str(objects.get("endpoint_url", ""))).hostname or ""
            )
        self.credential_domains = configured_credentials or _normalise_domains(
            [*seed_domains, *declared_service_domains]
        )
        self.credential_purposes = tuple(
            str(item).casefold()
            for item in settings.get(
                "credential_purposes",
                ["fetch", "login", "redirect", "robots", "browser", "stream", "ai", "storage", "plugin"],
            )
        )
        self.maximum_requests = int(settings.get("maximum_requests", 0))
        self.maximum_bytes = int(settings.get("maximum_bytes", 0))
        self.maximum_concurrency = int(settings.get("maximum_concurrency", 0))
        self.maximum_runtime = float(settings.get("maximum_runtime_seconds", 0))
        self.maximum_cost = float(settings.get("maximum_cost", 0))
        self.circuit_threshold = int(settings.get("circuit_failure_threshold", 5))
        self.circuit_recovery = float(settings.get("circuit_recovery_seconds", 30))
        self.audit_enabled = bool(settings.get("audit", True))
        self.audit_path = config.workspace / "logs" / "egress-audit.jsonl"
        self._started = time.monotonic()
        self._requests = 0
        self._response_bytes = 0
        self._cost = 0.0
        self._active = 0
        self._task_disabled = threading.Event()
        self._lock = threading.RLock()
        self._audit_lock = threading.Lock()
        self._audit_failures = 0
        self._capability_counts: dict[str, int] = {}
        self._circuits: dict[str, tuple[int, float]] = {}

    @classmethod
    def emergency_disconnect_all(cls) -> None:
        """Globally kill all network egress for every broker in the process."""
        cls._global_disabled.set()

    @classmethod
    def restore_global_network(cls) -> None:
        """Re-enable global egress after a previous emergency disconnect."""
        cls._global_disabled.clear()

    def disconnect_task(self) -> None:
        """Block new network requests for the current task only."""
        self._task_disabled.set()

    def reconnect_task(self) -> None:
        """Re-allow network requests for the current task."""
        self._task_disabled.clear()

    def issue_capability(
        self,
        subject: str,
        *,
        domains: list[str] | tuple[str, ...],
        purposes: list[str] | tuple[str, ...] = ("plugin",),
        maximum_requests: int = 0,
    ) -> NetworkCapability:
        """Mint a scoped capability token for a plugin or sub-component.

        Args:
            subject: Human-readable identifier of the token holder.
            domains: Allowed destination domains for this token.
            purposes: Allowed purpose tags (e.g. ``"plugin"``, ``"fetch"``).
            maximum_requests: Per-token request ceiling (0 = unlimited).

        Returns:
            An immutable :class:`NetworkCapability` token.
        """
        normalised = _normalise_domains(list(domains))
        if not subject.strip() or not normalised:
            raise ValueError("网络能力令牌必须声明主体和至少一个域名")
        if maximum_requests < 0:
            raise ValueError("maximum_requests不能为负数")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._capability_counts[token] = 0
        return NetworkCapability(
            token=token,
            subject=subject.strip(),
            domains=normalised,
            purposes=tuple(str(item).casefold() for item in purposes),
            maximum_requests=maximum_requests,
        )

    def revoke_capability(self, capability: NetworkCapability) -> None:
        """Invalidate a previously issued capability token."""
        with self._lock:
            self._capability_counts.pop(capability.token, None)

    def _control_stopped(self) -> bool:
        try:
            state = json.loads((self.config.workspace / "run_control.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(isinstance(state, dict) and state.get("stop_requested"))

    def _check_switches(self) -> None:
        if not self.enabled or self._task_disabled.is_set() or self._global_disabled.is_set():
            raise EgressDisabledError("网络出口已关闭")
        if self._control_stopped():
            raise EgressDisabledError("任务已停止，禁止产生新的网络请求")

    def _check_time_and_cost(self) -> None:
        elapsed = time.monotonic() - self._started
        if self.maximum_runtime and elapsed >= self.maximum_runtime:
            raise EgressBudgetExceededError("网络运行时间预算已用尽")
        if self.maximum_cost and self._cost >= self.maximum_cost:
            raise EgressBudgetExceededError("网络费用预算已用尽")

    def _check_circuit(self, host: str) -> None:
        failures, open_until = self._circuits.get(host, (0, 0.0))
        if open_until > time.monotonic():
            raise EgressBudgetExceededError(f"目标{host}的网络熔断器处于打开状态")
        if open_until:
            self._circuits[host] = (max(0, failures - 1), 0.0)

    def authorize(
        self,
        url: str,
        *,
        purpose: str = "fetch",
        headers: Mapping[str, str] | None = None,
        capability: NetworkCapability | None = None,
        count_request: bool = True,
    ) -> tuple[str, ...]:
        """Validate a URL against all egress policies and budgets.

        Checks the global/task kill-switches, scheme/port/domain allow-lists,
        the network target policy, credential-scoping rules, per-host circuit
        breakers, runtime/byte/request/cost budgets, and capability-token
        scope before counting the request.

        Args:
            url: The URL to authorize.
            purpose: Purpose tag (e.g. ``"fetch"``, ``"browser"``).
            headers: Request headers, used for credential-scope checks.
            capability: Optional capability token constraining domains/purposes.
            count_request: Increment the global request counter.

        Returns:
            Approved resolved addresses from the network target policy.

        Raises:
            EgressDisabledError: Kill-switch active or scheme/port/domain blocked.
            CredentialScopeError: Credentials would leak to an unapproved host.
            EgressBudgetExceededError: A budget ceiling was reached.
        """
        purpose = purpose.casefold()
        try:
            self._check_switches()
            parts = urllib.parse.urlsplit(url)
            scheme = parts.scheme.casefold()
            host = (parts.hostname or "").casefold()
            if scheme not in self.allowed_schemes or not host:
                raise EgressDisabledError(f"网络协议或目标无效: {scheme or '(empty)'}")
            port = parts.port or (443 if scheme in {"https", "wss"} else 80)
            if self.allowed_ports and port not in self.allowed_ports:
                raise EgressDisabledError(f"端口未获批准: {port}")
            if self.allowed_domains and not _domain_matches(host, self.allowed_domains):
                raise EgressDisabledError(f"域名未获批准: {host}")
            network_url = urllib.parse.urlunsplit(
                ("https" if scheme == "wss" else "http" if scheme == "ws" else scheme, parts.netloc, parts.path, parts.query, "")
            )
            self.policy.require(network_url)
        except (PermissionError, ValueError) as exc:
            self._audit("blocked", url=url, purpose=purpose, reason=str(exc)[:300])
            raise

        credential_headers = sorted(key for key in (headers or {}) if _SENSITIVE_HEADER.match(key))
        if credential_headers and (
            purpose not in self.credential_purposes
            or not self.credential_domains
            or not _domain_matches(host, self.credential_domains)
        ):
            # B01-012：凭据越权是最值得留痕的事件——补审计，避免"挡住了却查不到"。
            self._audit("blocked", url=url, purpose=purpose, reason=f"凭据不能发送到{host}用于{purpose}")
            raise CredentialScopeError(f"凭据不能发送到{host}用于{purpose}")

        with self._lock:
            self._check_time_and_cost()
            self._check_circuit(host)
            subject = "core"
            capability_used = 0
            if capability is not None:
                if capability.token not in self._capability_counts:
                    self._audit("blocked", url=url, purpose=purpose, reason="网络能力令牌无效或已撤销")
                    raise EgressDisabledError("网络能力令牌无效或已撤销")
                if not _domain_matches(host, capability.domains) or purpose not in capability.purposes:
                    self._audit("blocked", url=url, purpose=purpose, reason="网络能力令牌不允许该域名或用途")
                    raise EgressDisabledError("网络能力令牌不允许该域名或用途")
                capability_used = self._capability_counts[capability.token]
                if capability.maximum_requests and capability_used >= capability.maximum_requests:
                    # B01-012：预算超限补审计
                    self._audit("blocked", url=url, purpose=purpose, reason="网络能力令牌请求预算已用尽")
                    raise EgressBudgetExceededError("网络能力令牌请求预算已用尽")
                subject = capability.subject
            if count_request:
                if self.maximum_requests and self._requests >= self.maximum_requests:
                    # B01-012：预算超限补审计
                    self._audit("blocked", url=url, purpose=purpose, reason="网络请求预算已用尽")
                    raise EgressBudgetExceededError("网络请求预算已用尽")
                self._requests += 1
                if capability is not None:
                    self._capability_counts[capability.token] = capability_used + 1
        self._audit(
            "authorized",
            url=url,
            purpose=purpose,
            subject=subject,
            credential_headers=credential_headers,
        )
        return self.policy.approved_addresses(host, port)

    @contextmanager
    def request(
        self,
        url: str,
        *,
        purpose: str = "fetch",
        headers: Mapping[str, str] | None = None,
        capability: NetworkCapability | None = None,
        count_request: bool = True,
    ) -> Iterator[None]:
        """Context manager that enforces concurrency budget around ``authorize``.

        Acquires a concurrency slot, calls :meth:`authorize`, yields, and
        always releases the slot in ``finally``.

        Args:
            url: The URL to authorize.
            purpose: Purpose tag forwarded to :meth:`authorize`.
            headers: Request headers forwarded to :meth:`authorize`.
            capability: Optional capability token forwarded to :meth:`authorize`.
            count_request: Whether to increment the request counter.

        Yields:
            ``None`` — control returns to the caller inside the policy boundary.
        """
        with self._lock:
            self._check_switches()
            if self.maximum_concurrency and self._active >= self.maximum_concurrency:
                raise EgressBudgetExceededError("网络并发预算已用尽")
            self._active += 1
        try:
            self.authorize(
                url,
                purpose=purpose,
                headers=headers,
                capability=capability,
                count_request=count_request,
            )
            yield
        finally:
            with self._lock:
                self._active = max(0, self._active - 1)

    def record_response(self, size: int, *, cost: float = 0.0, url: str = "") -> None:
        """Account for response bytes and optional cost against the budget.

        Args:
            size: Response body size in bytes.
            cost: Monetary cost to accumulate (default 0).
            url: Source URL, used for the audit record.

        Raises:
            EgressBudgetExceededError: Byte or cost ceiling exceeded.
        """
        if size < 0 or cost < 0:
            raise ValueError("响应大小和费用不能为负数")
        with self._lock:
            next_bytes = self._response_bytes + size
            next_cost = self._cost + cost
            if self.maximum_bytes and next_bytes > self.maximum_bytes:
                raise EgressBudgetExceededError("网络流量预算已用尽")
            if self.maximum_cost and next_cost > self.maximum_cost:
                raise EgressBudgetExceededError("网络费用预算已用尽")
            self._response_bytes = next_bytes
            self._cost = next_cost
        self._audit("response", url=url, bytes=size, cost=cost)

    def record_success(self, url: str) -> None:
        """Clear the circuit-breaker failure count for the URL's host."""
        host = (urllib.parse.urlsplit(url).hostname or "").casefold()
        if not host:
            return
        with self._lock:
            self._circuits.pop(host, None)
        self._audit("circuit_success", url=url)

    def record_failure(self, url: str, *, retryable: bool = True, error: str = "") -> None:
        """Record a retryable failure and possibly open the circuit breaker.

        Args:
            url: The URL that failed.
            retryable: When ``False``, the failure is ignored.
            error: Short error description for the audit log.
        """
        if not retryable:
            return
        host = (urllib.parse.urlsplit(url).hostname or "").casefold()
        if not host:
            return
        with self._lock:
            failures, _open_until = self._circuits.get(host, (0, 0.0))
            failures += 1
            open_until = (
                time.monotonic() + self.circuit_recovery
                if failures >= self.circuit_threshold
                else 0.0
            )
            self._circuits[host] = (failures, open_until)
        self._audit(
            "circuit_failure",
            url=url,
            failures=failures,
            open=bool(open_until),
            error=error[:300],
        )

    @contextmanager
    def sdk_request(
        self,
        endpoint: str,
        *,
        transport: str,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[None]:
        """Wrap an external SDK whose socket cannot be DNS-pinned by urllib handlers.

        The endpoint, budget, stop switch and credentials are checked before the SDK
        call. The audit record explicitly marks the remaining SDK transport boundary.
        """

        self._audit(
            "sdk_transport_boundary",
            url=endpoint,
            transport=transport,
            limitation="sdk_controls_final_socket",
        )
        try:
            with self.request(endpoint, purpose="storage", headers=headers):
                yield
        except PermissionError:
            raise
        except Exception as exc:
            self.record_failure(endpoint, error=str(exc))
            raise
        else:
            self.record_success(endpoint)

    def snapshot(self) -> EgressSnapshot:
        """Return a point-in-time view of cumulative egress counters."""
        with self._lock:
            return EgressSnapshot(
                self._requests,
                self._response_bytes,
                self._cost,
                self._active,
                time.monotonic() - self._started,
            )

    def audit_status(self) -> dict[str, Any]:
        """Expose audit delivery health without making crawl availability depend on it.

        Audit files can be unavailable on a full or read-only workspace.  The
        broker must keep enforcing network policy in that situation, while the
        caller still needs an explicit, machine-readable signal that evidence
        collection was incomplete.
        """
        with self._audit_lock:
            return {
                "enabled": self.audit_enabled,
                "path": str(self.audit_path),
                "write_failures": self._audit_failures,
            }

    def _audit(self, event: str, *, url: str = "", **details: Any) -> None:
        if not self.audit_enabled:
            return
        record = {"timestamp": utcnow(), "event": event, "url": redact_url(url), **details}
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_lock, self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            # Network policy and budget enforcement are independent of a local
            # audit sink.  Failing closed here would turn a full log disk into a
            # hidden availability failure after request accounting has begun.
            with self._audit_lock:
                self._audit_failures += 1
            LOGGER.warning("Unable to append egress audit event: %s", exc)
