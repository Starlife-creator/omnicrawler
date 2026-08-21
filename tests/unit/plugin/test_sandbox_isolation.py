"""Phase 2a C1/C1a/C4：双后端隔离生效性 + session 生命周期 + IPC 协议。

验收锚点（方案）：
- 沙箱内 ``import omnicrawler`` 必失败（双后端）；``import yaml`` 必失败（-S 切断 site）
- 源码模式 ``[sys.executable, -I, -S, ...]``；冻结模式 fail-closed（宿主 exe 缺失拒载）
- session 模式：一次 spawn 多次顺序调用；session.end 收尾；崩溃后 E_RESOURCE 不自动重启
- 错误码语义：E_CONTRACT（handle 非 dict）/E_INTERNAL（插件异常）收敛为协议响应
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from omnicrawler.plugins import plugin_backend
from omnicrawler.plugins.plugin_sandbox import IsolatedPluginRunner, PluginSubprocessSession


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "demo_plugin.py").write_text(
        textwrap.dedent(
            """
            def handle(operation, payload):
                if operation == "add":
                    return {"sum": payload.get("a", 0) + payload.get("b", 0)}
                if operation == "echo":
                    return {"echo": payload}
                if operation == "fail":
                    raise ValueError("boom")
                if operation == "badtype":
                    return "not-a-dict"
                if operation == "probe":
                    import sys
                    try:
                        import omnicrawler  # noqa: F401
                        host = True
                    except ImportError:
                        host = False
                    try:
                        import yaml  # noqa: F401
                        thirdparty = True
                    except ImportError:
                        thirdparty = False
                    return {"host": host, "thirdparty": thirdparty, "path_len": len(sys.path)}
                return {}
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_backend_command_source_mode() -> None:
    """源码模式：sys.executable -I -S + 宿主脚本。"""
    command, timeout = plugin_backend.resolve_backend_command()
    assert command[0] == sys.executable
    assert command[1:3] == ["-I", "-S"]
    assert command[3].endswith("plugin_subprocess.py")
    assert timeout == plugin_backend.HANDSHAKE_TIMEOUT_SOURCE
    assert plugin_backend.backend_name() == "source_isolated"


def test_backend_frozen_missing_host_fails_closed(monkeypatch) -> None:
    """冻结模式宿主 exe 缺失 → 拒载（不静默回退宿主解释器）。"""
    monkeypatch.setattr(plugin_backend, "is_frozen", lambda: True)
    monkeypatch.setattr(plugin_backend, "bundled_sandbox_host", lambda: None)
    with pytest.raises(FileNotFoundError):
        plugin_backend.resolve_backend_command()


def test_isolation_blocks_host_and_site_packages(plugin_dir: Path) -> None:
    """隔离生效性：import omnicrawler / import yaml（site-packages）必失败。"""
    probe = IsolatedPluginRunner(plugin_dir, timeout_seconds=30).call("demo_plugin", "probe", {})
    assert probe["host"] is False, "沙箱内不应能 import omnicrawler"
    assert probe["thirdparty"] is False, "-S 应切断 site-packages（import yaml）"
    assert probe["path_len"] <= 6, "sys.path 应仅剩标准库 + 插件根"


def test_session_reuses_process_across_calls(plugin_dir: Path) -> None:
    """C1a：会话内多次调用复用同一进程，session.end 收尾。"""
    with PluginSubprocessSession(plugin_dir, "demo_plugin", timeout_seconds=30) as session:
        assert session.call("add", {"a": 1, "b": 2}) == {"sum": 3}
        assert session.call("add", {"a": 10, "b": 20}) == {"sum": 30}
        assert session.call("echo", {"k": "v"}) == {"echo": {"k": "v"}}
        first_proc = session._proc
        assert first_proc is not None and first_proc.poll() is None
    # with 退出后进程已回收
    assert session._proc is None


def test_error_codes_contract_and_internal(plugin_dir: Path) -> None:
    """错误收敛：插件异常 → E_INTERNAL；非 dict 返回 → E_CONTRACT。"""
    with PluginSubprocessSession(plugin_dir, "demo_plugin", timeout_seconds=30) as session:
        with pytest.raises(RuntimeError, match="E_INTERNAL"):
            session.call("fail", {})
        with pytest.raises(RuntimeError, match="E_CONTRACT"):
            session.call("badtype", {})
        # 错误后进程仍存活（错误不杀会话）
        assert session.call("add", {"a": 1, "b": 1}) == {"sum": 2}


def test_invalid_entry_module_rejected(plugin_dir: Path) -> None:
    """入口模块名受控校验（防路径注入）。"""
    with pytest.raises(ValueError):
        PluginSubprocessSession(plugin_dir, "../evil", timeout_seconds=5)
    with pytest.raises(ValueError):
        PluginSubprocessSession(plugin_dir, "a.b", timeout_seconds=5)


def test_missing_plugin_root_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PluginSubprocessSession(tmp_path / "nonexistent", "demo", timeout_seconds=5)
