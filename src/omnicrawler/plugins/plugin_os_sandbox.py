"""OS 沙箱抽象层（Phase 2a D2/D3/D4）。

三平台统一语义（方案第 25 轮）：沙箱后端全部为系统内置 API + 宿主内
ctypes/pywin32，零外部依赖、零安装动作——与"桌面单机、便携零依赖"定位一致。

后端策略（fail-closed，不静默降级）：
- Windows：AppContainer（完整隔离）→ 受限令牌 + Low IL（AC 不可用时的降级档，
  须显式开关）→ 两者皆不可用 → E_UNSUPPORTED_ENV 拒载
- Linux：unshare + seccomp + Landlock（内核 ≥5.13）→ seccomp-only（显式开关）
  → bwrap 后备 → 内核过低 fail-closed
- macOS：sandbox-exec / Seatbelt profile（系统内置）

探测以能力为准而非纯版本号（backport 探测到即可用）；每次 spawn 前置探测，
结果入审计。逃生开关 sandbox_escape 仅关闭 OS 沙箱层，保留 -I -S 子进程边界。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)

E_UNSUPPORTED_ENV = "E_UNSUPPORTED_ENV"


@dataclass(frozen=True, slots=True)
class SandboxProbe:
    """沙箱可用性探测结果（plugins audit + spawn 前置共用）。"""

    available: bool
    backend: str  # appcontainer | restricted_token | landlock | seccomp_only | seatbelt | none
    detail: str
    # 受支持范围判定（第 68 轮收窄）：Win10 22H2+/Win11、内核 ≥5.13 主流发行版
    supported_range: bool = True


def _probe_windows() -> SandboxProbe:
    """Windows：AppContainer API 存在性探测（GetProcAddress 精确判定）。"""
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        adv = k32.LoadLibraryW("advapi32.dll")
        try:
            ac_profile = bool(k32.GetProcAddress(adv, b"CreateAppContainerProfile"))
            ac_derive = bool(
                k32.GetProcAddress(adv, b"DeriveAppContainerSidFromAppContainerName")
            )
        finally:
            k32.FreeLibrary(adv)
        if ac_profile and ac_derive:
            return SandboxProbe(True, "appcontainer", "AppContainer API 可用")
        return SandboxProbe(
            False,
            "none",
            "AppContainer API 缺失（受支持范围异常——Win10 22H2+/Win11 应内置；"
            "请运行 plugins audit --report 反馈环境）",
        )
    except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理（fail-closed）
        return SandboxProbe(False, "none", f"探测异常: {exc}")


def _probe_linux() -> SandboxProbe:
    """Linux：能力探测——user namespaces + Landlock（内核 ≥5.13）。"""
    try:
        import os

        release = os.uname().release
        major, minor = (int(part) for part in release.split(".")[:2])
        kernel_ok = (major, minor) >= (5, 13)
        # user namespaces 可用性：试读 unprivileged userns 开关
        userns_ok = True
        try:
            with open("/proc/sys/kernel/unprivileged_userns_clone", encoding="utf-8") as fh:
                userns_ok = fh.read().strip() == "1"
        except OSError:
            pass  # 无该文件 = 发行版未禁用（Debian/Ubuntu 默认无此开关）
        if kernel_ok and userns_ok:
            return SandboxProbe(True, "landlock", f"内核 {release} ≥5.13，userns 可用")
        if kernel_ok:
            return SandboxProbe(
                True, "seccomp_only", f"内核 {release} ≥5.13 但 userns 受限（降级档）"
            )
        return SandboxProbe(
            False,
            "none",
            f"内核 {release} <5.13，非受支持范围（fail-closed，第 68 轮收窄）",
            supported_range=False,
        )
    except Exception as exc:  # noqa: BLE001
        return SandboxProbe(False, "none", f"探测异常: {exc}", supported_range=False)


def _probe_darwin() -> SandboxProbe:
    """macOS：sandbox-exec 存在性（系统内置）。"""
    import shutil

    if shutil.which("sandbox-exec"):
        return SandboxProbe(True, "seatbelt", "sandbox-exec (Seatbelt) 可用")
    return SandboxProbe(False, "none", "sandbox-exec 缺失")


def probe_os_sandbox() -> SandboxProbe:
    """当前平台沙箱可用性探测（纯探测，无副作用）。"""
    if sys.platform == "win32":
        return _probe_windows()
    if sys.platform.startswith("linux"):
        return _probe_linux()
    if sys.platform == "darwin":
        return _probe_darwin()
    return SandboxProbe(False, "none", f"未知平台 {sys.platform}", supported_range=False)


def resolve_sandbox_mode(
    *,
    probe: SandboxProbe | None = None,
    sandbox_escape: bool = False,
    allow_restricted_token_fallback: bool = False,
) -> tuple[str, str]:
    """裁决沙箱运行模式（spawn 前置，结果入审计）。

    返回 (mode, reason)：
    - ("os", ...)     OS 沙箱可用，启用完整隔离
    - ("degraded", .) 降级档（受限令牌/seccomp-only，须显式开关）
    - ("none", ...)   逃生开关开启（保留 -I -S 子进程边界）
    - ("refuse", ...) fail-closed 拒载（映射 E_UNSUPPORTED_ENV）

    语义（方案 D2/D3）：OS 沙箱不可用时不静默降级——降级档必须显式开关；
    既无 OS 沙箱又未开降级/逃生 → refuse。
    """
    if sandbox_escape:
        return "none", "sandbox_escape 逃生开关开启（保留子进程边界，OS 沙箱关闭）"

    probe = probe or probe_os_sandbox()
    if probe.available:
        if probe.backend in ("appcontainer", "landlock", "seatbelt"):
            return "os", f"OS 沙箱启用: {probe.backend}"
        # seccomp_only 属降级档，需显式开关
        if allow_restricted_token_fallback:
            return "degraded", f"降级档显式开启: {probe.backend}（{probe.detail}）"
        return "refuse", (
            f"{E_UNSUPPORTED_ENV}: 沙箱仅降级档可用而未显式开启（{probe.detail}）"
        )
    # AC 缺失：Windows 允许受限令牌降级档（显式开关）
    if allow_restricted_token_fallback and sys.platform == "win32":
        return "degraded", "AppContainer 不可用，受限令牌+Low IL 降级档（显式开启）"
    return "refuse", (
        f"{E_UNSUPPORTED_ENV}: 沙箱不可用（{probe.detail}）——"
        "受支持范围: Win10 22H2+/Win11、内核 ≥5.13 主流发行版；"
        "请运行 plugins audit --report 反馈环境信息"
    )
