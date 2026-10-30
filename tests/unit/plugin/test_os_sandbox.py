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


@pytest.mark.skipif(sys.platform != "win32", reason="真实 AppContainer 导出只在 Windows 上可探")
def test_windows_probe_finds_builtin_appcontainer_and_yields_os_mode() -> None:
    """Win10 22H2+/Win11 应探到 AC：恒判"缺失"会让 spawn 前置 fail-closed 拒载全部插件。"""
    probe = sandbox.probe_os_sandbox()
    assert probe.backend == "appcontainer", probe.detail
    assert probe.available is True
    # 默认开关（无 escape/降级）下不得 refuse，否则 Windows 装机即不可用
    assert sandbox.resolve_sandbox_mode()[0] == "os"


class _FakeApi:
    """记录 ctypes 签名声明与实参的假 kernel32（句柄/地址在这里只是标记值）。"""

    def __init__(self, resolved: tuple[bytes, ...]) -> None:
        self._resolved = resolved
        self.LoadLibraryW = _FakeFunction(lambda name: 0x7FF6_1234_5678)
        self.GetProcAddress = _FakeFunction(
            lambda module, symbol: 0x7FF6_9ABC_0000 if symbol in self._resolved else 0
        )
        self.FreeLibrary = _FakeFunction(lambda module: 1)


class _FakeFunction:
    def __init__(self, impl) -> None:
        self._impl = impl
        self.restype = None
        self.argtypes = None
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        return self._impl(*args)


def _install_fake_kernel32(monkeypatch: pytest.MonkeyPatch, resolved: tuple[bytes, ...]) -> _FakeApi:
    import ctypes

    fake = _FakeApi(resolved)
    windll = type("_Windll", (), {"kernel32": fake})()
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    return fake


def test_windows_probe_reads_pointer_sized_handles_from_exporting_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """锁定两处历史误判：导出模块写成 advapi32；句柄未按 c_void_p 声明而被截成 32 位。"""
    import ctypes

    fake = _install_fake_kernel32(monkeypatch, resolved=())
    probe = sandbox._probe_windows()
    assert fake.LoadLibraryW.calls == [(sandbox.AC_EXPORT_MODULE,)]
    assert fake.LoadLibraryW.restype is ctypes.c_void_p
    assert fake.GetProcAddress.restype is ctypes.c_void_p
    assert fake.GetProcAddress.argtypes == [ctypes.c_void_p, ctypes.c_char_p]
    assert probe.available is False


def test_windows_probe_requires_every_appcontainer_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只解析到一半符号不得判可用（缺口写进 detail 供 audit 回传定位）。"""
    first = sandbox.AC_EXPORT_SYMBOLS[0].encode("ascii")
    missing = sandbox.AC_EXPORT_SYMBOLS[1]
    _install_fake_kernel32(monkeypatch, resolved=(first,))
    probe = sandbox._probe_windows()
    assert probe.available is False
    assert missing in probe.detail
