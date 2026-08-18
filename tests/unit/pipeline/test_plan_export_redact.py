"""S2.2.4：plan_compiler 脱敏补全 + plan -o 导出凭据扫描。

验收：Authorization/Bearer 头与中文密钥键名被掩码；plan 导出无凭据泄漏。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from omnicrawler.commands.plan import execute
from omnicrawler.pipeline_ops.plan_compiler import _redact_for_hash


def _task(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: plan-test, workspace: '{tmp_path / 'ws'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "http: {headers: {Authorization: 'Bearer abc-supersecret'}}\n" + extra,
        encoding="utf-8",
    )
    return path


def test_s224_redact_covers_authorization_bearer_and_chinese_keys() -> None:
    payload = {
        "headers": {"Authorization": "Bearer abc123"},
        "login": {"密码": "hunter2", "token": "x", "用户名": "kept"},
        "proxy": {"password": "pw"},
        "safe": {"url": "https://example.org"},
    }
    redacted = _redact_for_hash(payload)
    assert redacted["headers"]["Authorization"] == "<redacted>"
    assert redacted["login"]["密码"] == "<redacted>"
    assert redacted["login"]["token"] == "<redacted>"
    assert redacted["login"]["用户名"] == "kept"
    assert redacted["safe"]["url"] == "https://example.org"


def test_s224_plan_export_has_no_plaintext(tmp_path: Path) -> None:
    cfg = _task(
        tmp_path,
        "ai: {mode: cloud, default_provider: openai, "
        "providers: {openai: {type: openai_compatible, api_key: sk-ultra-secret}}}\n",
    )
    output = tmp_path / "plan.json"
    result = execute(str(cfg), output=str(output))
    assert result["output"] == str(output)
    raw = output.read_text(encoding="utf-8")
    assert "sk-ultra-secret" not in raw
    assert "<redacted>" in raw


def test_s224_plan_export_diff_is_redacted(tmp_path: Path) -> None:
    first = _task(tmp_path, "http: {headers: {Cookie: 'session=abc'}}\n")
    second = tmp_path / "task2.yaml"
    second.write_text(
        f"project: {{name: plan-test, workspace: '{tmp_path / 'ws2'}'}}\n"
        "source: {kind: static_html, seeds: [https://other.org/]}\n"
        "http: {headers: {Cookie: 'session=abc'}}\n",
        encoding="utf-8",
    )
    output = tmp_path / "diff.json"
    execute(str(second), compare=str(first), output=str(output))
    raw = output.read_text(encoding="utf-8")
    assert "session=abc" not in raw


def test_s224_redact_leaves_plain_yaml_valid() -> None:
    payload = {"a": {"authorization": "Bearer x"}, "b": [1, 2]}
    dumped = yaml.safe_dump(_redact_for_hash(payload), allow_unicode=True, sort_keys=False)
    assert yaml.safe_load(dumped)["a"]["authorization"] == "<redacted>"
