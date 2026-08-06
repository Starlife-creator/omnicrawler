"""S2.1.2：配置错误信息增强。

① 错误多行带编号（load_config 抛出 ConfigParseError，不再挤一行）；
② YAML 语法错误友好包装（含行列号，原始异常在 __cause__）；
③ ${VAR} 缺失汇总为 warning；
后项：describe_error 覆盖 urllib/SSL/空 message/KeyError（P2#65/#78）、
登录失败可达（P2#70）、benchmark 10%% 文案（源A P0#16）。
"""

from __future__ import annotations

import ssl
import urllib.error
from pathlib import Path

import pytest

from omnicrawl.core.config import load_config, validate_config
from omnicrawl.core.errors import (
    ConfigParseError,
    LoginFailedError,
    describe_error,
)
from omnicrawl.core.utils import expand_env, expand_env_checked

# ---------- ② YAML 语法错误友好包装 ----------


def test_s212_bad_yaml_raises_friendly_config_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "project: {name: t}\nsource: {kind: crawl, seeds: [https://example.com/]}\n  broken: [\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigParseError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "语法错误" in message
    assert "第" in message and "行" in message
    assert isinstance(excinfo.value.__cause__, __import__("yaml").YAMLError)


def test_s212_non_mapping_top_level_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="顶层必须是YAML对象"):
        load_config(path)


# ---------- ① 错误多行带编号 ----------


def test_s212_validation_errors_multiline_numbered(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(
        "project: {name: t, workspace: work}\n"
        "source: {kind: crawl, seeds: [https://example.com/]}\n"
        "crawl: {strategy: sideways, max_pages: 0}\n"
        "auth: {options: not-a-mapping}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigParseError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "配置校验失败" in message
    assert message.count("[") == 3
    assert "crawl.strategy" in message and "max_pages" in message
    assert "auth.options必须是YAML对象" in message


def test_s212_english_error_messages_are_chinese(tmp_path: Path) -> None:
    from omnicrawl.core.config import DEFAULTS, AppConfig, deep_merge

    raw = deep_merge(
        DEFAULTS,
        {
            "project": {"name": "t"},
            "source": {"kind": "crawl", "seeds": ["https://example.com/"]},
            "resources": {"profile": "ultra"},
            "storage": {"records": {"backends": "nope"}},
            "outputs": {"plugin_exporters": 3},
        },
    )
    config = AppConfig(Path("<m>"), Path.cwd(), raw, Path.cwd())
    errors, _warnings = validate_config(config)
    assert any("resources.profile只能是" in item for item in errors)
    assert any("storage.records.backends必须是数组" in item for item in errors)
    assert any("outputs.plugin_exporters必须是数组" in item for item in errors)


# ---------- ③ ${VAR} 缺失汇总 ----------


def test_s212_missing_env_vars_warn_with_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S212_MISSING_ONE", raising=False)
    monkeypatch.delenv("S212_MISSING_TWO", raising=False)
    monkeypatch.setenv("S212_PRESENT", "defined")
    path = tmp_path / "env.yaml"
    path.write_text(
        "project: {name: t, workspace: work}\n"
        "source: {kind: crawl, seeds: ['${S212_PRESENT}/page']}\n"
        "http: {user_agent: 'Bot/1.0 (+contact: ${S212_MISSING_ONE}@example.com)'}\n"
        "crawl: {max_pages: 1}\n"
        "download: {output_dir: '${S212_MISSING_TWO}'}\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.raw["source"]["seeds"] == ["defined/page"]
    assert config.raw["http"]["user_agent"] == "Bot/1.0 (+contact: @example.com)"
    summary = [w for w in config.warnings if "环境变量未定义" in w]
    assert len(summary) == 1
    assert "2 个" in summary[0]
    assert "S212_MISSING_ONE" in summary[0] and "S212_MISSING_TWO" in summary[0]


def test_s212_env_defaults_are_not_warned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S212_WITH_DEFAULT", raising=False)
    path = tmp_path / "env.yaml"
    path.write_text(
        "project: {name: t, workspace: work}\n"
        "source: {kind: crawl, seeds: [https://example.com/]}\n"
        "crawl: {max_pages: '${S212_WITH_DEFAULT:-5}'}\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.raw["crawl"]["max_pages"] == "5"
    assert not any("环境变量未定义" in w for w in config.warnings)


def test_s212_expand_env_checked_reports_missing() -> None:
    value, missing = expand_env_checked("${S212_A}-${S212_B:-d}-x-${S212_A}")
    assert value == "-d-x-"
    assert missing == ["S212_A"]
    assert expand_env("${S212_A}") == ""


# ---------- 后项：describe_error ----------


def test_s212_describe_error_urlopen_timeout_is_transient() -> None:
    info = describe_error(urllib.error.URLError(TimeoutError("timed out")))
    assert info.code == "network_transient"
    assert info.retryable is True
    assert "timed out" in info.message


def test_s212_describe_error_tls_verification() -> None:
    reason = ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate")
    info = describe_error(urllib.error.URLError(reason))
    assert info.code == "tls_verification"
    assert info.retryable is False
    assert "verify_tls=false" in info.suggestion


def test_s212_describe_error_plain_ssl_error() -> None:
    info = describe_error(ssl.SSLError("WRONG_VERSION_NUMBER"))
    assert info.code == "tls_error"
    assert "TLS" in info.suggestion


def test_s212_describe_error_empty_message_fallback() -> None:
    info = describe_error(TimeoutError())
    assert info.message == "TimeoutError"
    info2 = describe_error(ConnectionError())
    assert info2.message == "ConnectionError"


def test_s212_describe_error_key_error_carries_key_name() -> None:
    info = describe_error(KeyError("max_pages"))
    assert info.code == "key_error"
    assert "max_pages" in info.message
    assert "拼写" in info.suggestion


def test_s212_login_failed_error_is_reachable() -> None:
    error = LoginFailedError("登录失败: HTTP 403")
    info = describe_error(error)
    assert info.code == "login_failed"
    assert "HTTP 403" in info.message
    assert "source.login" in info.suggestion


# ---------- 后项：benchmark 10%% ----------


def test_s212_benchmark_help_shows_single_percent_sign() -> None:
    import argparse

    from omnicrawl.cli._main import build_parser

    parser = build_parser()
    texts = [parser.format_help()]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            texts.extend(item.format_help() for item in action.choices.values())
    help_text = "\n".join(texts)
    # argparse 会对 help 做 % 格式化：源码写 10%%、渲染为 10%，
    # 既不能崩溃（裸 10% 后跟中文括号会 ValueError），也不能显示字面 10%%
    assert "10%%" not in help_text
    assert "10%" in help_text


if __name__ == "__main__":
    pytest.main([__file__])
