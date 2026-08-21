"""Phase 2a D2/D3：OS 沙箱抽象层契约测试（探测 + fail-closed 裁决）。"""

from __future__ import annotations

import sys

import pytest

from omnicrawler.plugins import plugin_os_sandbox as sandbox
from omnicrawler.plugins.plugin_os_sandbox import SandboxProbe

pytestmark = pytest.mark.plugin_contract


def test_probe_returns_result_on_current_platform() -> None:
    probe = sandbox.probe_os_sandbox()
    assert isinstance(probe, SandboxProbe)
    assert probe.backend in (
        "appcontainer", "restricted_token", "landlock", "seccomp_only", "seatbelt", "none"
    )
    assert isinstance(probe.detail, str) and probe.detail


def test_sandbox_escape_disables_os_sandbox_keeps_subprocess() -> None:
    """逃生开关：OS 沙箱关闭但保留 -I -S 子进程边界（语义区分）。"""
    mode, reason = sandbox.resolve_sandbox_mode(sandbox_escape=True)
    assert mode == "none"
    assert "sandbox_escape" in reason


def test_os_sandbox_enabled_when_probe_ok() -> None:
    probe = SandboxProbe(True, "appcontainer", "ok")
    mode, reason = sandbox.resolve_sandbox_mode(probe=probe)
    assert mode == "os"
    assert "appcontainer" in reason


def test_degraded_requires_explicit_flag() -> None:
    """降级档（seccomp_only）未显式开启 → fail-closed 拒载。"""
    probe = SandboxProbe(True, "seccomp_only", "降级档")
    mode, _ = sandbox.resolve_sandbox_mode(probe=probe, allow_restricted_token_fallback=False)
    assert mode == "refuse"

    mode, reason = sandbox.resolve_sandbox_mode(probe=probe, allow_restricted_token_fallback=True)
    assert mode == "degraded"
    assert "降级档显式开启" in reason


def test_unavailable_refuses_fail_closed() -> None:
    """沙箱不可用且未开降级/逃生 → refuse（E_UNSUPPORTED_ENV 语义）。"""
    probe = SandboxProbe(False, "none", "AC 缺失")
    mode, reason = sandbox.resolve_sandbox_mode(probe=probe, allow_restricted_token_fallback=False)
    assert mode == "refuse"
    assert sandbox.E_UNSUPPORTED_ENV in reason
    assert "audit --report" in reason  # 拒载引导回传通道（第 71 轮）


def test_windows_restricted_token_fallback_needs_explicit_flag() -> None:
    """Windows AC 缺失：受限令牌降级档需显式开关。"""
    probe = SandboxProbe(False, "none", "AC 缺失")
    if sys.platform == "win32":
        mode, reason = sandbox.resolve_sandbox_mode(
            probe=probe, allow_restricted_token_fallback=True
        )
        assert mode == "degraded"
        assert "受限令牌" in reason
    else:
        mode, _ = sandbox.resolve_sandbox_mode(probe=probe, allow_restricted_token_fallback=True)
        # 非 Windows 无受限令牌降级档 → 仍 refuse
        assert mode == "refuse"


def test_unsupported_range_flagged() -> None:
    """内核过低（非受支持范围）→ supported_range=False。"""
    probe = SandboxProbe(False, "none", "内核 3.10", supported_range=False)
    assert probe.supported_range is False
    mode, reason = sandbox.resolve_sandbox_mode(probe=probe)
    assert mode == "refuse"
