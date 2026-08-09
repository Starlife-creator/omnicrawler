"""services.ai_providers.provider_from_env 统一构造入口测试（Phase 1 C1/C2/C3/C9）。

验证：未启用/缺必填返回 None、custom 归一、secret:// 解析、
本机端点放行内网、返回的 provider 已携带 app_config/egress（generate 可直接调用）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.services.ai_providers import OpenAICompatibleProvider, provider_from_env


@pytest.fixture(autouse=True)
def _fake_data_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "omnicrawl.core.runtime_paths.portable_data_root",
        lambda: tmp_path / "data",
    )


def _env(**overrides) -> dict[str, str]:
    values = {
        "OMNICRAWL_AI_PROVIDER": "openai_compatible",
        "OMNICRAWL_AI_BASE_URL": "https://api.example.com/v1",
        "OMNICRAWL_AI_MODEL": "gpt-x",
        "OMNICRAWL_AI_API_KEY": "sk-test",
        "OMNICRAWL_AI_TIMEOUT": "42",
    }
    values.update(overrides)
    return values


def test_disabled_returns_none() -> None:
    assert provider_from_env(_env(OMNICRAWL_AI_PROVIDER="disabled")) is None


def test_missing_required_returns_none() -> None:
    assert provider_from_env(_env(OMNICRAWL_AI_BASE_URL="")) is None
    assert provider_from_env(_env(OMNICRAWL_AI_MODEL="")) is None


def test_builds_provider_with_egress_ready() -> None:
    provider = provider_from_env(_env())
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.app_config is not None  # generate() 不再抛"必须通过Egress"错误
    assert provider.egress is not None
    assert provider.timeout == 42.0
    assert provider.api_key == "sk-test"


def test_custom_type_normalized_to_openai_compatible() -> None:
    provider = provider_from_env(_env(OMNICRAWL_AI_PROVIDER="custom"))
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://api.example.com/v1"


def test_secret_ref_is_resolved(monkeypatch) -> None:
    monkeypatch.setenv("OMNICRAWL_SECRET_MYKEY", "resolved-key")
    provider = provider_from_env(_env(OMNICRAWL_AI_API_KEY="secret://MYKEY"))
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.api_key == "resolved-key"


def test_private_endpoint_allows_private_network() -> None:
    provider = provider_from_env(_env(OMNICRAWL_AI_BASE_URL="http://127.0.0.1:11434/v1"))
    assert isinstance(provider, OpenAICompatibleProvider)
    # 本机端点必须放行内网，否则 Egress 策略会拦截本地模型
    assert provider.app_config is not None
    assert provider.app_config.section("http").get("allow_private_network") is True


def test_invalid_timeout_falls_back_to_default() -> None:
    provider = provider_from_env(_env(OMNICRAWL_AI_TIMEOUT="abc"))
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.timeout == 60.0


def test_authorize_allows_credential_ai_request_local() -> None:
    """带 Authorization 头的 AI 请求不应被凭据作用域拦截（blocking 修复回归）。"""
    provider = provider_from_env(_env(OMNICRAWL_AI_BASE_URL="http://127.0.0.1:11434/v1"))
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.egress is not None
    addresses = provider.egress.authorize(
        "http://127.0.0.1:11434/v1/chat/completions",
        purpose="ai",
        headers={"Authorization": "Bearer sk-test"},
    )
    assert "127.0.0.1" in addresses
