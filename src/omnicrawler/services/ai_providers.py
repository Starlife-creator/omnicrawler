"""Optional AI provider routing with an OpenAI-compatible HTTP adapter.

The crawler never needs AI to fetch, deduplicate or detect byte/semantic changes.
Providers are opt-in enhancement services and secrets should be supplied through
the existing ``secret://`` resolver.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..core.config import DEFAULTS, AppConfig
from ..core.errors import ResponseTooLargeError
from ..fetching.http_client import build_safe_opener
from ..security.egress import EgressBroker
from .ai_safety import AIBudget, AIBudgetExceededError

# C5/C6/C7 配套常量：AI 请求对网络瞬断做指数退避重试（HTTP/解析错误不重试）
AI_RETRY_ATTEMPTS = 3
AI_RETRY_BASE_DELAY = 1.0

# HTTP 状态码 -> 中文处置建议（C6：透出响应体时附上可操作指引）
_STATUS_GUIDANCE: dict[int, str] = {
    400: "请求格式错误，请检查 messages / response_format 是否符合 OpenAI 规范。",
    401: "API 密钥无效或未授权，请检查 OMNICRAWL_AI_API_KEY。",
    403: "密钥无权限访问该模型或端点。",
    404: "端点不存在，请确认 base_url 指向 /chat/completions（或前缀正确）。",
    408: "服务端请求超时，可稍后重试。",
    429: "请求过于频繁（限流）。请降低并发或稍后重试；长期使用建议申请提额。",
    500: "服务端内部错误，可稍后重试。",
    502: "网关错误，可稍后重试。",
    503: "服务不可用，可稍后重试。",
    504: "网关超时，可稍后重试。",
}


# FINAL-S11：响应体是远端可控内容——base_url 配错指向回显端点时，Bearer/API key
# 可能被回显进错误消息并落入日志。嵌入前对常见凭据形态做脱敏。
_TOKEN_LIKE = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._\-]+"),
)


def _scrub_token_like(text: str) -> str:
    for pattern in _TOKEN_LIKE:
        text = pattern.sub("[REDACTED]", text)
    return text


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    """读取 HTTPError 响应体前 1KB，用于透出 429/余额/密钥 等详情（C6）。"""
    try:
        data = exc.read(1024)
    except Exception:
        return ""
    if not data:
        return ""
    return _scrub_token_like(data.decode("utf-8", errors="replace").strip()[:800])


def _format_http_error(name: str, exc: urllib.error.HTTPError, body: str) -> str:
    code = getattr(exc, "code", None)
    guidance = _STATUS_GUIDANCE.get(int(code) if isinstance(code, int) else 0, "请查看响应体定位原因。")
    snippet = f"\n响应体: {body}" if body else ""
    return f"AI provider {name} 返回 HTTP {code}。{guidance}{snippet}"


def _format_network_error(name: str, exc: Exception, timeout: float) -> str:
    if isinstance(exc, socket.timeout):
        return f"AI provider {name} 请求超时（{timeout}s）。请检查网络或调大 AI 超时设置。"
    if isinstance(exc, ssl.SSLError):
        return f"AI provider {name} SSL 握手失败：{exc}。请检查 base_url 是否使用 https 且证书有效。"
    if isinstance(exc, ConnectionError):
        return f"AI provider {name} 连接失败：{exc}。请检查 base_url 可达性与网络/代理。"
    return f"AI provider {name} 网络请求失败：{exc}。"


@dataclass(frozen=True, slots=True)
class AIResult:
    text: str
    provider: str
    model: str
    usage: dict[str, Any]
    raw: dict[str, Any]


class DisabledProvider:
    name = "disabled"

    def generate(self, *_args: Any, **_kwargs: Any) -> AIResult:
        raise RuntimeError("AI 已关闭；请在「AI 服务中心」选择本地、云端或自定义服务")


# 费用预算按 token 估算（与审计一致；实际计费以 provider 账单为准）
_ESTIMATED_COST_PER_TOKEN = 0.000002


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        *,
        app_config: AppConfig | None = None,
        egress: EgressBroker | None = None,
        budget: AIBudget | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.name = name
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.api_key = str(config.get("api_key", ""))
        self.model = str(config.get("model", ""))
        self.timeout = float(config.get("timeout_seconds", 60))
        # C10：单次最大 token（UI "最大响应长度" 落点）；<=0 视为不限制
        self.max_tokens = int(max_tokens) if max_tokens and int(max_tokens) > 0 else None
        self.app_config = app_config
        self.egress = egress
        # C33：费用/次数上限默认无上限但保持计数；配置了上限后 consume 生效
        self.budget = budget or AIBudget()
        # C8：base_url 必须是合法 http(s) URL，提前失败给出清晰错误而非请求时诡异异常
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                f"AI provider {name} 的 base_url 必须是合法的 http(s) URL，收到: {self.base_url!r}"
            )
        if not self.model:
            raise ValueError(f"AI provider {name} 需要 base_url 和 model")

    def check_content_allowed(self, content_kind: str, what: str) -> None:
        """AI 外发隐私闸门（B05-019 接线）：未显式开启对应开关则拒发。

        调用方在构造 messages 前调用，content_kind 取 privacy 键
        （allow_page_text / allow_pdf_content / allow_screenshots / allow_cookies）。

        Raises:
            AIPrivacyBlockedError: privacy 开关未显式开启，或缺少 app_config 无法判定。
        """
        from ..core.ai_env import require_ai_privacy
        from ..core.errors import AIPrivacyBlockedError

        if self.app_config is None:
            raise AIPrivacyBlockedError(
                "AI provider 缺少 app_config，无法判定隐私策略；拒绝外发敏感内容。"
            )
        require_ai_privacy(self.app_config.workspace, content_kind=content_kind, what=what)

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> AIResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        # C10：将单次最大 token 写入请求体，使 UI "最大响应长度" 设置生效
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        if response_format:
            payload["response_format"] = response_format
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        if self.app_config is None or self.egress is None:
            raise RuntimeError("AI网络请求必须通过应用Egress Broker创建provider")
        maximum = int(self.app_config.section("http").get("max_response_bytes", 50_000_000))
        # C33：请求前预占——只递增请求次数；token/费用已耗尽则直接拒发
        self.budget.consume(tokens=0, cost=0.0)
        if self.budget.maximum_tokens and self.budget.tokens >= self.budget.maximum_tokens:
            raise AIBudgetExceededError("AI Token 预算已用完")
        if self.budget.maximum_cost and self.budget.cost >= self.budget.maximum_cost:
            raise AIBudgetExceededError("AI 费用预算已用完")
        # C5/C6/C7：带重试退避与异常覆盖、HTTP 响应体透出的网络请求
        value = self._open_json(request, maximum)
        # C33：响应后结算 token——先记账再检查，超限也记账（熔断：后续预占/结算持续失败）
        usage = value.get("usage", {}) if isinstance(value, dict) else {}
        try:
            tokens = int(usage.get("total_tokens", 0) or 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens:
            self.budget.tokens += tokens
            self.budget.cost += tokens * _ESTIMATED_COST_PER_TOKEN
            if self.budget.maximum_tokens and self.budget.tokens > self.budget.maximum_tokens:
                raise AIBudgetExceededError("AI Token 预算已用完")
            if self.budget.maximum_cost and self.budget.cost > self.budget.maximum_cost:
                raise AIBudgetExceededError("AI 费用预算已用完")
        choices = value.get("choices", [])
        if not choices:
            raise RuntimeError(f"AI provider {self.name} 未返回 choices")
        text = str(choices[0].get("message", {}).get("content", ""))
        return AIResult(text, self.name, self.model, dict(value.get("usage", {})), value)


    def _open_json(self, request: urllib.request.Request, maximum: int) -> dict[str, Any]:
        """带重试退避的 JSON 请求（C5/C6/C7 综合修复）。

        - 网络瞬断（超时 / SSL / 连接错）按指数退避重试；HTTP 错误与解析错误不重试
        - HTTPError 透出响应体前 ~1KB 与中文处置建议（C6）
        - 覆盖 socket.timeout / ssl.SSLError / UnicodeDecodeError 等原被逃逸的异常（C5）
        """
        assert self.egress is not None, "generate() 已校验 egress 非空"
        for attempt in range(AI_RETRY_ATTEMPTS):
            try:
                with self.egress.request(request.full_url, purpose="ai", headers=request.headers):
                    opener = build_safe_opener(
                        self.app_config,  # type: ignore[arg-type]
                        target_policy=self.egress.policy,
                        include_cookies=False,
                        egress=self.egress,
                        purpose="ai",
                    )
                    with opener.open(request, timeout=self.timeout) as response:
                        raw = response.read(maximum + 1)
                        if len(raw) > maximum:
                            raise ResponseTooLargeError(f"AI响应超过大小限制: > {maximum}")
                        self.egress.record_response(len(raw), url=response.geturl())
                        return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # HTTP 错误（含 429/401/403/404/5xx）不重试，直接透出详情
                body = _read_error_body(exc)
                raise RuntimeError(_format_http_error(self.name, exc, body)) from exc
            except (ssl.SSLError, TimeoutError, ConnectionError, OSError, urllib.error.URLError) as exc:
                if attempt + 1 < AI_RETRY_ATTEMPTS:
                    time.sleep(min(8.0, AI_RETRY_BASE_DELAY * (2 ** attempt)))
                    continue
                raise RuntimeError(_format_network_error(self.name, exc, self.timeout)) from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"AI provider {self.name} 响应不是合法 JSON（可能返回了错误页/HTML）。"
                    f"请确认 base_url 指向 /chat/completions 端点。原始错误: {exc}"
                ) from exc
        # 理论不可达（循环内已 raise）；保留以保证类型与逻辑完整
        raise RuntimeError(f"AI provider {self.name} 请求失败（重试耗尽）")


def build_provider(
    ai_config: dict[str, Any],
    capability: str = "",
    *,
    app_config: AppConfig | None = None,
    egress: EgressBroker | None = None,
) -> Any:
    mode = str(ai_config.get("mode", "disabled")).casefold()
    if mode == "disabled":
        return DisabledProvider()
    routing = ai_config.get("routing", {})
    provider_name = str(
        routing.get(capability) if isinstance(routing, dict) and capability in routing
        else ai_config.get("default_provider", "")
    )
    providers = ai_config.get("providers", {})
    if not provider_name or not isinstance(providers, dict) or provider_name not in providers:
        raise ValueError(f"AI 能力 {capability or 'default'} 未配置可用 provider")
    provider_config = providers[provider_name]
    if not isinstance(provider_config, dict):
        raise ValueError(f"AI provider {provider_name} 配置必须是映射")
    provider_type = str(provider_config.get("type", "openai_compatible")).casefold()
    if provider_type == "custom":
        # 历史遗留别名：UI 曾提供 custom，实际等同 OpenAI 兼容端点
        provider_type = "openai_compatible"
    if provider_type not in {"openai_compatible", "openai", "local"}:
        raise ValueError(f"不支持的 AI provider 类型: {provider_type}")
    # C10：从 ai.budget.max_tokens_per_request 读取单次最大 token（缺省回退 DEFAULTS）
    raw_budget = ai_config.get("budget")
    budget_section = raw_budget if isinstance(raw_budget, dict) else {}
    raw_max = int(budget_section.get("max_tokens_per_request", DEFAULTS["ai"]["budget"]["max_tokens_per_request"]))
    request_max_tokens = raw_max if raw_max > 0 else None
    return OpenAICompatibleProvider(
        provider_name,
        provider_config,
        app_config=app_config,
        egress=egress,
        max_tokens=request_max_tokens,
    )


def provider_from_env(
    env_vars: dict[str, str] | None = None,
    *,
    project_root: str | None = None,
) -> Any:
    """从 .env 单一真源构造 AI provider（统一入口，含 Egress 审计与 secret 解析）。

    - 未启用/缺必填 → 返回 None（调用方按未启用处理）
    - custom 类型归一为 openai_compatible
    - secret:// 引用先解析；本机/内网端点（Ollama 等）自动放行内网
    - 返回的 provider 已携带 app_config/egress，generate() 可直接调用
    """
    import copy
    from pathlib import Path

    from ..core.ai_env import load_ai_env
    from ..core.config import DEFAULTS, AppConfig
    from ..core.credentials import resolve_secret_refs
    from ..core.runtime_paths import portable_data_root
    from ..security.egress import EgressBroker
    from ..security.policy import is_private_target

    if not env_vars:
        env_vars = load_ai_env(project_root)
    provider_type = str(env_vars.get("OMNICRAWL_AI_PROVIDER", "disabled")).casefold()
    if provider_type == "disabled":
        return None
    base_url = str(env_vars.get("OMNICRAWL_AI_BASE_URL", "")).strip()
    model = str(env_vars.get("OMNICRAWL_AI_MODEL", "")).strip()
    if not base_url or not model:
        return None
    api_key = resolve_secret_refs(str(env_vars.get("OMNICRAWL_AI_API_KEY", "")))
    timeout = 60
    try:
        timeout = int(env_vars.get("OMNICRAWL_AI_TIMEOUT", "60"))
    except (TypeError, ValueError):
        pass  # .env 脏数据回退默认，不让 AI 静默不可用

    ai_config = {
        "mode": "enabled",
        "default_provider": "default",
        "providers": {
            "default": {
                "type": provider_type,
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "timeout_seconds": timeout,
            }
        },
    }
    raw = copy.deepcopy(DEFAULTS)
    raw["project"]["name"] = "home-quick-task"
    # 把 AI 端点登记进 ai.providers：EgressBroker 据此计算 credential_domains，
    # 否则带 Authorization 头的 AI 请求会被凭据作用域拦截（CredentialScopeError）。
    # 仅登记域名所需字段，api_key 由 provider 持有，不写入 raw。
    # 只覆盖 providers/mode，保留 DEFAULTS 中 routing/fallback/privacy/budget 等键。
    raw["ai"] = {
        **raw["ai"],
        "mode": "enabled",
        "default_provider": "default",
        "providers": {
            "default": {
                "type": provider_type,
                "base_url": base_url,
                "model": model,
            }
        },
    }
    # AI 目标由用户显式配置：本机/内网端点（Ollama 等）需放行内网
    if is_private_target(base_url):
        raw["http"]["allow_private_network"] = True
    workspace = portable_data_root() / ".omnicrawler" / "ai-logs"
    workspace.mkdir(parents=True, exist_ok=True)
    app_config = AppConfig(Path("<home-quick-task>"), Path.cwd(), raw, workspace, ())
    egress = EgressBroker(app_config)

    # WP-11（P1-1 收窄）：消费 sidecar 的 budget/routing/extraction 运行时接线。
    # 未声明或 schema<1 时不覆盖，保持 DEFAULTS 默认；budget 超限回退仍由 AIBudget 兜底。
    from ..core.ai_env import load_ai_config_sidecar

    sidecar = load_ai_config_sidecar(project_root) or {}
    try:
        _schema = int(sidecar.get("schema", 0))
    except (TypeError, ValueError):
        _schema = 0
    if _schema >= 1:
        for _key in ("budget", "routing", "extraction"):
            if isinstance(sidecar.get(_key), dict):
                ai_config[_key] = sidecar[_key]

    return build_provider(ai_config, app_config=app_config, egress=egress)
