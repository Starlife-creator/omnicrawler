"""A-P2-4：子进程 sys.modules 裁剪纯函数与 in_process 信任目录裁决回归测试。"""
from __future__ import annotations

from pathlib import Path

from omnicrawler.plugins import plugin_router
from omnicrawler.plugins.plugin_subprocess import (
    BANNED_SUBPROCESS_MODULES,
    _apply_windows_job_object,
    strip_banned_modules,
)


def test_strip_banned_modules_removes_banned_and_keeps_safe() -> None:
    modules: dict[str, object] = {
        "os": object(),
        "os.path": object(),
        "subprocess": object(),
        "socket": object(),
        "ctypes": object(),
        "threading": object(),
        "json": object(),
        "sys": object(),
        "omnicrawler_sdk": object(),
    }
    removed = strip_banned_modules(modules)

    for banned in BANNED_SUBPROCESS_MODULES:
        assert banned not in modules
    assert "json" in modules and "sys" in modules and "omnicrawler_sdk" in modules
    assert set(removed) == {"os", "subprocess", "socket", "ctypes", "threading"}
    # 重复调用幂等：返回空列表、不残留
    assert strip_banned_modules(modules) == []


def test_strip_operates_on_passed_dict_without_global_side_effects() -> None:
    import sys

    before = set(sys.modules)
    strip_banned_modules(dict(sys.modules))  # 传拷贝：不触达宿主全局缓存
    assert set(sys.modules) == before


def test_trusted_local_dirs_parses_and_dedupes() -> None:
    dirs = plugin_router.trusted_local_dirs(
        {"trusted_local_dirs": ["/home/user/plugins", "", "/home/user/plugins", "  ./dev  "]}
    )
    assert dirs == ["/home/user/plugins", "./dev"]
    assert plugin_router.trusted_local_dirs({"trusted_local_dirs": "not-a-list"}) == []
    assert plugin_router.trusted_local_dirs(None) == []


def test_is_under_local_trusted_dir_positive_and_negative(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    plugin = root / "a" / "plugin.py"
    outside = tmp_path / "untrusted" / "plugin.py"
    dirs = [str(root)]
    assert plugin_router.is_under_local_trusted_dir(plugin, dirs) is True
    assert plugin_router.is_under_local_trusted_dir(outside, dirs) is False
    # fail-closed：空清单一律不允许进程内
    assert plugin_router.is_under_local_trusted_dir(plugin, []) is False


def test_decide_route_legacy_escape_hatch_returns_in_process_with_audit_reason() -> None:
    decision = plugin_router.decide_route(
        execution_mode="subprocess",
        runtime_backend=plugin_router.RUNTIME_BACKEND_LEGACY_IN_PROCESS,
    )
    assert decision.backend == "in_process"
    assert "legacy_in_process" in decision.reason
    assert decision.tier == "config"


def test_windows_job_object_guard_returns_unsupported_on_non_windows() -> None:
    # A-P3-14：非 Windows 平台不导入 ctypes、不触碰 Win32 API；真实 Job Object 待 Windows CI。
    import sys

    status = _apply_windows_job_object()
    if sys.platform == "win32":
        assert status == "skeleton_pending_windows_ci"
    else:
        assert status == "unsupported_platform"
