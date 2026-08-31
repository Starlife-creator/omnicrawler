"""Phase 2a C2/C3/C4：能力代理（Capability Broker）契约测试。

验收锚点：
- system.info 内置无需声明；其余能力运行期 ⊆ 静态审批（超即 E_PERMISSION）
- 未知操作 → E_CONTRACT；broker 调用轨迹降采样计数（op_counts）
- C2 V2：verified_bytes 临时入口执行（磁盘原件被忽略，TOCTOU 关闭）+ 会话结束清理
"""

from __future__ import annotations

import base64
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
    assert result["capability_versions"]["system.info"] == 1
    assert result["capability_versions"]["records.read"] == 1
    assert broker.op_counts["system.info"] == 1


def test_required_capability_versions_fail_closed() -> None:
    plugin_broker.validate_required_capabilities(
        {"system.info": 1, "records.read": ">=1"}
    )
    with pytest.raises(ValueError, match="不支持"):
        plugin_broker.validate_required_capabilities({"future.magic": 1})
    with pytest.raises(ValueError, match="版本不足"):
        plugin_broker.validate_required_capabilities({"records.read": ">=2"})
    with pytest.raises(ValueError, match="版本要求非法"):
        plugin_broker.validate_required_capabilities({"records.read": "latest"})


def test_opaque_artifact_stream_commits_without_exposing_path(tmp_path: Path) -> None:
    broker = _make_broker(
        permissions={"artifacts:write"},
        artifact_root=tmp_path / "artifacts",
    )
    opened = broker.dispatch(
        "artifact.stream.open", {"name": "export.jsonl", "media_type": "application/jsonl"}
    )
    assert set(opened) == {"handle", "maximum_bytes"}
    broker.dispatch(
        "artifact.stream.write",
        {
            "handle": opened["handle"],
            "content_b64": base64.b64encode(b'{"ok":true}\n').decode("ascii"),
        },
    )
    committed = broker.dispatch("artifact.stream.commit", {"handle": opened["handle"]})
    assert "path" not in committed
    assert committed["artifact_id"].startswith("sha256:")
    assert (tmp_path / "artifacts" / "export.jsonl").read_bytes() == b'{"ok":true}\n'
    assert broker.committed_artifacts[0]["path"].endswith("export.jsonl")


def test_opaque_artifact_stream_aborts_and_enforces_quota(tmp_path: Path) -> None:
    broker = _make_broker(
        permissions={"artifacts:write"},
        artifact_root=tmp_path / "artifacts",
        maximum_artifact_bytes=3,
    )
    opened = broker.dispatch("artifact.stream.open", {"name": "small.bin"})
    with pytest.raises(plugin_broker.CapabilityError, match="最大字节数"):
        broker.dispatch(
            "artifact.stream.write",
            {
                "handle": opened["handle"],
                "content_b64": base64.b64encode(b"four").decode("ascii"),
            },
        )
    assert not list((tmp_path / "artifacts").glob("*"))


def test_opaque_artifact_stream_requires_explicit_permission(tmp_path: Path) -> None:
    broker = _make_broker(artifact_root=tmp_path)
    with pytest.raises(plugin_broker.CapabilityError) as error:
        broker.dispatch("artifact.stream.open", {"name": "blocked.bin"})
    assert error.value.code == plugin_broker.E_PERMISSION


def test_records_page_uses_single_use_opaque_cursor() -> None:
    class State:
        def rows(self, _sql, params):
            after = int(params[1])
            limit = int(params[-1])
            rows = [
                {
                    "rowid": index,
                    "record_id": f"r{index}",
                    "source_url": "https://example.test/",
                    "data_json": f'{{"index":{index}}}',
                }
                for index in range(after + 1, 6)
            ]
            return rows[:limit]

    broker = _make_broker(
        permissions={"records:read"}, state_store=State(), run_id="run-1"
    )
    first = broker.dispatch("records.page", {"limit": 2})
    assert [item["record_id"] for item in first["records"]] == ["r1", "r2"]
    assert first["next_cursor"] and "rowid" not in first["next_cursor"]
    cursor = first["next_cursor"]
    second = broker.dispatch("records.page", {"limit": 2, "cursor": cursor})
    assert [item["record_id"] for item in second["records"]] == ["r3", "r4"]
    with pytest.raises(plugin_broker.CapabilityError, match="已使用"):
        broker.dispatch("records.page", {"limit": 2, "cursor": cursor})


def test_response_metadata_and_payload_are_separate_permissions(tmp_path: Path) -> None:
    archive = tmp_path / "raw" / "page.html"
    archive.parent.mkdir()
    archive.write_bytes(b"<html>safe archive</html>")

    class State:
        path = tmp_path / "state.db"

        def rows(self, _sql, _params):
            return [
                {
                    "id": 1,
                    "url": "https://example.test/",
                    "final_url": "https://example.test/",
                    "status_code": 200,
                    "content_type": "text/html",
                    "size_bytes": archive.stat().st_size,
                    "content_sha256": "a" * 64,
                    "raw_path": str(archive),
                    "changed": 1,
                    "elapsed_seconds": 0.1,
                    "fetched_at": "2026-08-31T00:00:00Z",
                }
            ]

    metadata_only = _make_broker(
        permissions={"responses:read"}, state_store=State(), run_id="run-1"
    )
    page = metadata_only.dispatch("responses.page", {"limit": 10})
    response = page["responses"][0]
    assert response["payload_available"] is True
    assert "raw_path" not in response
    with pytest.raises(plugin_broker.CapabilityError) as error:
        metadata_only.dispatch(
            "responses.payload", {"response_ref": response["response_ref"]}
        )
    assert error.value.code == plugin_broker.E_PERMISSION

    with_payload = _make_broker(
        permissions={"responses:read", "responses:payload"},
        state_store=State(),
        run_id="run-1",
    )
    page = with_payload.dispatch("responses.page", {})
    payload = with_payload.dispatch(
        "responses.payload",
        {"response_ref": page["responses"][0]["response_ref"]},
    )
    assert base64.b64decode(payload["content_b64"]) == archive.read_bytes()
    assert payload["truncated"] is False


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
