"""Non-generating discovery and explicitly bounded generation diagnostics."""
from __future__ import annotations

import json
import math
from pathlib import Path
from threading import Event
from typing import Any

from ..core.credentials import resolve_secret_refs
from ..security.controlled_http import scoped_json_request, scoped_network_config
from ..security.egress import EgressBroker
from .ai_providers import OpenAICompatibleProvider
from .ai_safety import AIBudget


def discover(base_url: str, api_key: str, workspace: Path, *, timeout: int = 20, allow_private: bool = False) -> list[str]:
    key = resolve_secret_refs({"api_key": api_key})["api_key"]
    value = scoped_json_request(base_url.rstrip("/") + "/models", workspace=workspace, purpose="ai",
                                headers={"Authorization": "Bearer " + key} if key else {},
                                timeout_seconds=min(timeout, 30), allow_private_network=allow_private)
    if not isinstance(value.get("data"), list):
        raise ValueError("模型列表响应缺少data数组")
    return [str(item["id"]) for item in value["data"] if isinstance(item, dict) and isinstance(item.get("id"), str)]


def estimate(pricing: dict[str, Any], *, output_tokens: int = 16) -> dict[str, Any]:
    from .ai_accounting import AIRequestAccounting

    budget = AIBudget(maximum_requests=1, maximum_tokens=4096)
    accounting = AIRequestAccounting(budget, pricing)
    payload = {"messages": [{"role": "user", "content": 'Return only {"ok":true}.'}], "max_tokens": output_tokens}
    reservation = accounting.reserve(payload)
    tokens, cost = budget._reservations[reservation]
    return {"reserved_tokens": tokens, "estimated_cost": cost if accounting.priced else None,
            "output_tokens": output_tokens, "pricing_source": "local_configuration", "billing_verified": False}


def test_generation(base_url: str, api_key: str, model: str, workspace: Path, *, pricing: dict[str, Any],
                    maximum_cost: float, structured: bool = False, allow_private: bool = False,
                    timeout: int = 20, cancel_event: Event | None = None) -> dict[str, Any]:
    if type(maximum_cost) not in (float, int) or not math.isfinite(maximum_cost) or maximum_cost <= 0:
        raise ValueError("生成验收必须设置有限正数费用上限")
    prediction = estimate(pricing)
    if prediction["estimated_cost"] is None or prediction["estimated_cost"] > maximum_cost:
        raise ValueError("缺少模型单价或预估费用超过验收预算")
    config = scoped_network_config(base_url.rstrip("/") + "/chat/completions", workspace=workspace,
                                   purpose="ai", timeout_seconds=min(timeout, 30), allow_private_network=allow_private)
    key = resolve_secret_refs({"api_key": api_key})["api_key"]
    provider = OpenAICompatibleProvider("acceptance", {"base_url": base_url, "api_key": key, "model": model,
        "timeout_seconds": min(timeout, 30), "total_timeout_seconds": min(timeout, 30), "pricing": pricing,
        "supports_json_schema": structured}, app_config=config, egress=EgressBroker(config),
        budget=AIBudget(maximum_requests=1, maximum_tokens=4096, maximum_cost=maximum_cost), max_tokens=16, cancel_event=cancel_event)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    result = provider.generate([{"role": "user", "content": 'Return only {"ok":true}.'}],
                               response_schema=schema if structured else None, schema_strict=structured)
    value = json.loads(result.text)
    if not isinstance(value, dict) or set(value) != {"ok"} or value["ok"] is not True:
        raise ValueError("生成结果未通过固定验收内容校验")
    return {"status": "passed", "model": model, "structured_output_tested": structured, "prediction": prediction,
            "accounting": result.accounting, "payload_scope": "fixed_test_prompt_no_user_content"}


def failure_message(exc: Exception) -> str:
    status = getattr(exc, "status", getattr(exc, "code", None))
    cause = exc.__cause__
    for _index in range(4):
        if cause is None or type(status) is int:
            break
        status = getattr(cause, "status", getattr(cause, "code", None))
        cause = cause.__cause__
    suggestions = {401: "认证失败，请检查密钥引用与账号有效性。", 403: "权限不足，请检查模型访问权限。",
                   404: "接口或模型不存在；模型列表不可用时可单独验证生成接口。", 429: "服务限流或额度不足，请稍后再试。"}
    if type(status) is int and status in suggestions:
        return suggestions[status]
    return "检查未通过（" + type(exc).__name__ + "），请检查地址、出口权限、模型单价与预算；错误中不展示凭据。"
