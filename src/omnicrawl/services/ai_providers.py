"""Optional AI provider routing with an OpenAI-compatible HTTP adapter.

The crawler never needs AI to fetch, deduplicate or detect byte/semantic changes.
Providers are opt-in enhancement services and secrets should be supplied through
the existing ``secret://`` resolver.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..core.config import AppConfig
from ..core.errors import ResponseTooLargeError
from ..fetching.http_client import build_safe_opener
from ..security.egress import EgressBroker
from .ai_safety import AIBudget, AIBudgetExceededError


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
        raise RuntimeError("AI 已关闭；请在 ai.mode 中选择本地、云端或自定义服务")


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
    ) -> None:
        self.name = name
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.api_key = str(config.get("api_key", ""))
        self.model = str(config.get("model", ""))
        self.timeout = float(config.get("timeout_seconds", 60))
        self.app_config = app_config
        self.egress = egress
        # C33：费用/次数上限默认无上限但保持计数；配置了上限后 consume 生效
        self.budget = budget or AIBudget()
        if not self.base_url or not self.model:
            raise ValueError(f"AI provider {name} 需要 base_url 和 model")

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
        opener = build_safe_opener(
            self.app_config,
            target_policy=self.egress.policy,
            include_cookies=False,
            egress=self.egress,
            purpose="ai",
        )
        maximum = int(self.app_config.section("http").get("max_response_bytes", 50_000_000))
        # C33：请求前预占——只递增请求次数；token/费用已耗尽则直接拒发
        self.budget.consume(tokens=0, cost=0.0)
        if self.budget.maximum_tokens and self.budget.tokens >= self.budget.maximum_tokens:
            raise AIBudgetExceededError("AI Token 预算已用完")
        if self.budget.maximum_cost and self.budget.cost >= self.budget.maximum_cost:
            raise AIBudgetExceededError("AI 费用预算已用完")
        try:
            with self.egress.request(request.full_url, purpose="ai", headers=headers):
                with opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(maximum + 1)
                    if len(raw) > maximum:
                        raise ResponseTooLargeError(f"AI响应超过大小限制: > {maximum}")
                    self.egress.record_response(len(raw), url=response.geturl())
                    value = json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"AI provider {self.name} 请求失败: {exc}") from exc
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
    return OpenAICompatibleProvider(
        provider_name,
        provider_config,
        app_config=app_config,
        egress=egress,
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
    workspace = portable_data_root() / ".omnicrawl" / "ai-logs"
    workspace.mkdir(parents=True, exist_ok=True)
    app_config = AppConfig(Path("<home-quick-task>"), Path.cwd(), raw, workspace, ())
    egress = EgressBroker(app_config)
    return build_provider(ai_config, app_config=app_config, egress=egress)
