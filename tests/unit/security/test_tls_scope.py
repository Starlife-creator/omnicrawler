"""P2-5：TLS 校验降级必须留在显式声明的作用域内。

`http.verify_tls` 是**全局**开关（6 条抓取路径共用同一个布尔值），
因此「为访问一台自签证书的内网机器而关掉校验」会连带把公网目标一起降级。
本文件锁定收紧后的三条性质：

1. **配置层 fail-closed**：`verify_tls=false` 必须点名允许免校验的主机；条目格式受校验。
2. **出网层强制作用域**：名单外的主机在出网收口处被拦截——降级不再是无形的全局状态。
3. **默认路径零影响**：`verify_tls` 保持默认 `true` 时一切照旧。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import DEFAULTS, AppConfig, load_config, validate_config
from omnicrawler.core.errors import ConfigParseError, PolicyBlockedError
from omnicrawler.security import tls_scope
from omnicrawler.security.egress import EgressBroker


class _Policy:
    """替身 policy：避免单测真的做 DNS（与 tests/unit/egress/test_egress.py 同一惯例）。"""

    def require(self, url: str) -> None:
        return None

    def approved_addresses(self, host: str, port: int) -> tuple[str, ...]:
        return (f"approved:{host}:{port}",)


def _broker(config: AppConfig) -> EgressBroker:
    return EgressBroker(config, policy=_Policy())  # type: ignore[arg-type]


def _config(tmp_path: Path, http: dict | None = None) -> AppConfig:
    raw = {
        "project": {"name": "tls-scope", "workspace": "work"},
        "source": {"kind": "static_html", "seeds": ["https://api.example.com/start"]},
        "http": {**DEFAULTS["http"], "user_agent": "test@example.com", **(http or {})},
        "egress": dict(DEFAULTS["egress"]),
    }
    return AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")


def _load(tmp_path: Path, http: dict) -> tuple[list[str], list[str]]:
    """写一份真实配置并走 `load_config` + `validate_config`（与用户路径一致）。"""
    payload = {
        "project": {"name": "tls-scope", "workspace": "work"},
        "source": {"kind": "static_html", "seeds": ["https://api.example.com/start"]},
        "http": {"user_agent": "test@example.com", **http},
    }
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    config = load_config(path)
    return validate_config(config)


# --------------------------------------------------------------------------
# 1) 作用域模块本身
# --------------------------------------------------------------------------


def test_scope_normalises_and_deduplicates() -> None:
    assert tls_scope.normalized([" A.local ", "a.local", "B.Example"]) == ("a.local", "b.example")


@pytest.mark.parametrize(
    "entry",
    ["", "   ", "https://a.local/x", "a.local:8443", "*.local", "a local", 5, None],
)
def test_invalid_entries_are_rejected(entry: object) -> None:
    assert tls_scope.invalid_entry_reason(entry) is not None


@pytest.mark.parametrize("entry", ["build-server", "build-server.local", "10.0.0.7", "internal.corp"])
def test_valid_entries_are_accepted(entry: str) -> None:
    assert tls_scope.invalid_entry_reason(entry) is None


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("10.1.2.3", True),
        ("127.0.0.1", True),
        ("fe80::1", True),
        ("build-server", True),
        ("nas.local", True),
        ("api.example.com", False),
        ("example.com", False),
    ],
)
def test_looks_internal(host: str, expected: bool) -> None:
    assert tls_scope.looks_internal(host) is expected


def test_allows_unverified_matches_host_and_subdomains() -> None:
    scope = ("internal.example",)
    assert tls_scope.allows_unverified("internal.example", scope)
    assert tls_scope.allows_unverified("a.internal.example", scope)
    assert not tls_scope.allows_unverified("example.com", scope)
    assert not tls_scope.allows_unverified("notinternal.example", scope)


# --------------------------------------------------------------------------
# 2) 配置层：fail-closed + 提示
# --------------------------------------------------------------------------


def test_disabling_verification_requires_explicit_scope(tmp_path: Path) -> None:
    with pytest.raises(ConfigParseError) as excinfo:
        _load(tmp_path, {"verify_tls": False})
    assert "tls_insecure_domains" in str(excinfo.value)


def test_disabling_verification_with_scope_is_accepted(tmp_path: Path) -> None:
    errors, _warnings = _load(
        tmp_path, {"verify_tls": False, "tls_insecure_domains": ["build-server.local"]}
    )
    assert not errors


def test_public_looking_scope_gets_a_warning(tmp_path: Path) -> None:
    _errors, warnings = _load(
        tmp_path, {"verify_tls": False, "tls_insecure_domains": ["example.com"]}
    )
    assert any("中间人风险" in item for item in warnings)


def test_malformed_scope_entry_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigParseError) as excinfo:
        _load(tmp_path, {"verify_tls": False, "tls_insecure_domains": ["https://a.local/x"]})
    assert "tls_insecure_domains" in str(excinfo.value)


def test_scope_without_disabling_is_reported_as_ineffective(tmp_path: Path) -> None:
    """名单配了但校验没关——必须提示「当前不生效」，避免用户以为已经放宽。"""
    _errors, warnings = _load(tmp_path, {"tls_insecure_domains": ["build-server.local"]})
    assert any("不生效" in item for item in warnings)


def test_default_config_has_empty_scope() -> None:
    assert DEFAULTS["http"]["tls_insecure_domains"] == []
    assert DEFAULTS["http"]["verify_tls"] is True


# --------------------------------------------------------------------------
# 3) 出网层：名单外拦截（唯一收口，6 条抓取路径一并受约束）
# --------------------------------------------------------------------------


def test_default_verification_leaves_egress_unchanged(tmp_path: Path) -> None:
    _broker(_config(tmp_path)).authorize("https://api.example.com/x")  # 不抛即通过


def test_unverified_scope_allows_listed_host(tmp_path: Path) -> None:
    config = _config(tmp_path, {"verify_tls": False, "tls_insecure_domains": ["internal.example"]})
    _broker(config).authorize("https://internal.example/x")


def test_unverified_scope_blocks_other_hosts(tmp_path: Path) -> None:
    """★ 核心：关掉校验不再等于「对所有目标降级」。"""
    config = _config(tmp_path, {"verify_tls": False, "tls_insecure_domains": ["internal.example"]})
    with pytest.raises(PolicyBlockedError) as excinfo:
        _broker(config).authorize("https://api.example.com/x")
    message = str(excinfo.value)
    assert "tls_insecure_domains" in message
    assert "api.example.com" in message


def test_unverified_scope_blocks_subdomain_of_other_host(tmp_path: Path) -> None:
    config = _config(tmp_path, {"verify_tls": False, "tls_insecure_domains": ["internal.example"]})
    with pytest.raises(PolicyBlockedError):
        _broker(config).authorize("https://deep.api.example.com/x")


def test_scope_is_read_from_the_http_section() -> None:
    section = {"verify_tls": False, "tls_insecure_domains": ["A.local", "a.local"]}
    assert tls_scope.verification_disabled(section) is True
    assert tls_scope.scope_of(section) == ("a.local",)
    assert tls_scope.verification_disabled({"verify_tls": True}) is False


def test_config_built_without_http_section_is_treated_as_verified() -> None:
    """缺 `http` 段（老旧构造）时按「校验开启」处理，不给错误的安全性暗示。"""
    assert tls_scope.verification_disabled(None) is False
    assert tls_scope.scope_of(None) == ()
