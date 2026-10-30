"""A-P1-2：沙箱裁决矩阵、spawn 前置接线与 fail-closed 拒载直接单测。

真实 Landlock/seccomp/AppContainer/Job Object/Seatbelt 包装待对应平台 CI 验证，
本文件只覆盖裁决纯逻辑与 spawn 前置接线口径，不伪装 OS 隔离已完成。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from omnicrawler.plugins import plugin_sandbox
from omnicrawler.plugins.plugin_os_sandbox import (
    SandboxProbe,
    confinement_argv_prefix,
    resolve_sandbox_mode,
)


def test_escape_switch_returns_none_with_subprocess_boundary_reason() -> None:
    mode, reason = resolve_sandbox_mode(sandbox_escape=True)
    assert mode == "none"
    assert "逃生开关" in reason


def test_available_full_backend_resolves_to_os_mode() -> None:
    probe = SandboxProbe(True, "landlock", "内核 ≥5.13")
    mode, reason = resolve_sandbox_mode(probe=probe)
    assert mode == "os"
    assert "landlock" in reason


def test_seccomp_only_requires_explicit_fallback_switch() -> None:
    probe = SandboxProbe(True, "seccomp_only", "userns 受限")
    mode, reason = resolve_sandbox_mode(probe=probe)
    assert mode == "refuse"
    assert "E_UNSUPPORTED_ENV" in reason

    mode, reason = resolve_sandbox_mode(probe=probe, allow_restricted_token_fallback=True)
    assert mode == "degraded"
    assert "seccomp_only" in reason


def test_unavailable_probe_fails_closed_without_fallback() -> None:
    probe = SandboxProbe(False, "none", "内核过低")
    mode, reason = resolve_sandbox_mode(probe=probe)
    assert mode == "refuse"
    assert "E_UNSUPPORTED_ENV" in reason


def test_confinement_prefix_is_empty_skeleton_pending_platform_ci() -> None:
    assert confinement_argv_prefix("none", "") == []
    assert confinement_argv_prefix("degraded", "seccomp_only") == []
    assert confinement_argv_prefix("os", "landlock") == []


def test_os_mode_reason_never_claims_enabled_while_prefix_is_empty() -> None:
    """口径护栏：spawn 前缀为空时，reason 不得声称 OS 沙箱「启用」。

    回归形态：`resolve_sandbox_mode` 曾返回 "OS 沙箱启用: landlock"，而
    `confinement_argv_prefix()` 恒为 [] —— 日志说了没做的事，且 SECURITY.md
    明文禁止这种表述。真接线（前缀非空）之后本断言自然失效，由那时的新口径接管。
    """
    for backend in ("appcontainer", "landlock", "seatbelt"):
        mode, reason = resolve_sandbox_mode(probe=SandboxProbe(True, backend, "探测可用"))
        assert mode == "os"
        if not confinement_argv_prefix(mode, backend):
            assert "启用" not in reason, f"{backend}: 前缀为空却声称启用：{reason}"


def _session(tmp_path: Path, **kwargs: object) -> plugin_sandbox.PluginSubprocessSession:
    root = tmp_path / "probe"
    root.mkdir()
    return plugin_sandbox.PluginSubprocessSession(root, "probe", **kwargs)  # type: ignore[arg-type]


def test_session_refuses_to_spawn_when_mode_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plugin_sandbox, "resolve_sandbox_mode",
        lambda **_kwargs: ("refuse", "E_UNSUPPORTED_ENV: 沙箱不可用"),
    )
    session = _session(tmp_path)
    with pytest.raises(RuntimeError, match="E_UNSUPPORTED_ENV"):
        session.start()
    assert session.sandbox_mode == "refuse"


def test_session_records_decision_and_invokes_popen_with_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin_sandbox, "resolve_sandbox_mode",
        lambda **_kwargs: ("none", "逃生开关开启"),
    )
    monkeypatch.setattr(
        plugin_sandbox.plugin_backend, "resolve_backend_command",
        lambda: (["python", "-I", "-S", "plugin_subprocess.py"], 30.0),
    )
    fake_popen = MagicMock()
    fake_popen.return_value.poll.return_value = 0  # 视为已退出，end() 不发 session.end
    monkeypatch.setattr(plugin_sandbox.subprocess, "Popen", fake_popen)

    session = _session(tmp_path, sandbox_escape=True)
    session.start()
    assert session.sandbox_mode == "none"
    argv = fake_popen.call_args.args[0]
    assert argv[:3] == ["python", "-I", "-S"]
    session.end()
