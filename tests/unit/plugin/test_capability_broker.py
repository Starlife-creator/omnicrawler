"""Phase 2a C2/C3/C4：能力代理（Capability Broker）契约测试。

验收锚点：
- system.info 内置无需声明；其余能力运行期 ⊆ 静态审批（超即 E_PERMISSION）
- 未知操作 → E_CONTRACT；broker 调用轨迹降采样计数（op_counts）
- C2 V2：verified_bytes 临时入口执行（磁盘原件被忽略，TOCTOU 关闭）+ 会话结束清理
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from omnicrawler.plugins import plugin_broker
from omnicrawler.plugins.plugin_sandbox import PluginSubprocessSession


@pytest.fixture()
def cap_plugin(tmp_path: Path) -> Path:
    (tmp_path / "cap_plugin.py").write_text(
        textwrap.dedent(
            """
            import omnicrawler_sdk


            def handle(operation, payload):
                if operation == "info":
                    return omnicrawler_sdk.system_info()
                if operation == "records":
                    try:
                        return omnicrawler_sdk.call("records.read", {"limit": 1})
                    except RuntimeError as exc:
                        return {"blocked": str(exc)}
                if operation == "unknown":
                    try:
                        return omnicrawler_sdk.call("nope.op", {})
                    except RuntimeError as exc:
                        return {"blocked": str(exc)}
                return {}
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


def _make_broker(**overrides):
    kwargs = {"permissions": set(), "system_info": {"version": "test"}}
    kwargs.update(overrides)
    return plugin_broker.CapabilityBroker(**kwargs)


def test_system_info_builtin_no_permission(cap_plugin: Path) -> None:
    broker = _make_broker()
    with PluginSubprocessSession(cap_plugin, "cap_plugin", timeout_seconds=15) as session:
        session.start()
        result = plugin_broker.drive_loop(session, broker, "info", {}, timeout_seconds=0)
    assert result["version"] == "test"
    assert broker.op_counts["system.info"] == 1


def test_permission_denied_without_declaration(cap_plugin: Path) -> None:
    broker = _make_broker(permissions=set())  # 未声明 records:read
    with PluginSubprocessSession(cap_plugin, "cap_plugin", timeout_seconds=15) as session:
        session.start()
        result = plugin_broker.drive_loop(session, broker, "records", {}, timeout_seconds=0)
    assert "E_PERMISSION" in result["blocked"]


def test_unknown_operation_contract_error(cap_plugin: Path) -> None:
    broker = _make_broker()
    with PluginSubprocessSession(cap_plugin, "cap_plugin", timeout_seconds=15) as session:
        session.start()
        result = plugin_broker.drive_loop(session, broker, "unknown", {}, timeout_seconds=0)
    assert "E_CONTRACT" in result["blocked"]


def test_trace_downsampling_counts(cap_plugin: Path) -> None:
    """调用轨迹降采样：操作类型计数累加。"""
    broker = _make_broker()
    with PluginSubprocessSession(cap_plugin, "cap_plugin", timeout_seconds=15) as session:
        session.start()
        for _ in range(3):
            plugin_broker.drive_loop(session, broker, "info", {}, timeout_seconds=0)
    assert broker.op_counts["system.info"] == 3


def test_verified_bytes_executed_not_disk(tmp_path: Path) -> None:
    """C2 V2：子进程执行验签字节而非磁盘原件（TOCTOU 关闭）。"""
    (tmp_path / "v_plugin.py").write_text(
        'def handle(op, p): return {"which": "DISK"}\n', encoding="utf-8"
    )
    verified = b'def handle(op, p): return {"which": "VERIFIED"}\n'
    session = PluginSubprocessSession(
        tmp_path, "v_plugin", timeout_seconds=15, verified_bytes=verified
    )
    session.start()
    entry_dir = session._verified_entry_dir
    assert entry_dir is not None and entry_dir.is_dir()
    try:
        assert session.call("x", {})["which"] == "VERIFIED"
    finally:
        session.end()
    assert not entry_dir.exists(), "会话结束必须清理验签临时入口"
