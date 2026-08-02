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


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        *,
        app_config: AppConfig | None = None,
        egress: EgressBroker | None = None,
    ) -> None:
        self.name = name
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.api_key = str(config.get("api_key", ""))
        self.model = str(config.get("model", ""))
        self.timeout = float(config.get("timeout_seconds", 60))
        self.app_config = app_config
        self.egress = egress
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
    if provider_type not in {"openai_compatible", "openai", "local"}:
        raise ValueError(f"不支持的 AI provider 类型: {provider_type}")
    return OpenAICompatibleProvider(
        provider_name,
        provider_config,
        app_config=app_config,
        egress=egress,
    )
