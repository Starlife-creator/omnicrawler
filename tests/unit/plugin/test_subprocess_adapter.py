"""Phase 2a B4 集成层：子进程组件适配器契约测试。

验收锚点：
- 契约 2 source 经会话被 pipeline 接口（seed()）消费，返回真实 CrawlRequest
- CrawlRequest/FetchResult 跨进程序列化往返一致（body 经 base64）
- 适配器 close() 回收会话进程
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from omnicrawler.core.errors import PolicyBlockedError
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.plugins.plugin_subprocess_adapter import (
    SubprocessExporterAdapter,
    SubprocessHookAdapter,
    SubprocessProcessorAdapter,
    SubprocessSourceAdapter,
    _SubprocessSessionHost,
    dict_to_request,
    dict_to_result,
    request_to_dict,
)


@pytest.fixture()
def c2_source_plugin(tmp_path: Path) -> Path:
    (tmp_path / "c2_source.py").write_text(
        textwrap.dedent(
            """
            def handle(operation, payload):
                if operation == "source.seed":
                    cfg = payload.get("config", {})
                    seeds = cfg.get("seeds", ["https://example.com/1"])
                    return {"requests": [
                        {"url": u, "method": "GET",
                         "headers": {"X-Requested-With": "XMLHttpRequest"},
                         "kind": "page", "meta": {"site": "c2"}} for u in seeds
                    ]}
                return {}
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_request_serialization_roundtrip() -> None:
    req = CrawlRequest(
        url="https://example.com/api",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=b'{"q":1}',
        kind="api",
        render=True,
        priority=2.5,
        depth=3,
        parent_url="https://example.com",
        meta={"page": 2},
    )
    restored = dict_to_request(request_to_dict(req))
    assert restored.url == req.url
    assert restored.method == req.method
    assert restored.headers == req.headers
    assert restored.body == req.body
    assert restored.kind == req.kind
    assert restored.render == req.render
    assert restored.priority == req.priority
    assert restored.depth == req.depth
    assert restored.parent_url == req.parent_url
    assert restored.meta == req.meta


def test_result_deserialization_base64_body() -> None:
    req = CrawlRequest(url="https://example.com")
    import base64

    result = dict_to_result(
        {"status": 201, "body_b64": base64.b64encode(b"hello").decode(), "url": "https://final"},
        req,
    )
    assert result.status == 201
    assert result.body == b"hello"
    assert result.final_url == "https://final"


def test_c2_source_adapter_pipeline_consumption(c2_source_plugin: Path) -> None:
    """pipeline 以工厂方式消费契约 2 source，返回真实 CrawlRequest。"""
    host = _SubprocessSessionHost(
        c2_source_plugin, "c2_source", permissions=set(), timeout_seconds=15
    )
    adapter = SubprocessSourceAdapter(host)
    requests = adapter.seed()
    assert len(requests) == 1
    assert requests[0].url == "https://example.com/1"
    assert requests[0].headers == {"X-Requested-With": "XMLHttpRequest"}
    assert requests[0].meta == {"site": "c2"}
    adapter.close()


def test_adapter_close_reclaims_session(c2_source_plugin: Path) -> None:
    host = _SubprocessSessionHost(
        c2_source_plugin, "c2_source", permissions=set(), timeout_seconds=15
    )
    adapter = SubprocessSourceAdapter(host)
    adapter.seed()  # 触发懒 spawn
    session = host._session
    assert session is not None and session._proc is not None
    adapter.close()
    assert host._session is None


@pytest.fixture()
def c2_extension_plugin(tmp_path: Path) -> Path:
    (tmp_path / "c2_extensions.py").write_text(
        textwrap.dedent(
            """
            def handle(operation, payload):
                if operation == "processor.process":
                    result = payload["result"]
                    request = result["request"]
                    return {"records": [{
                        "source_url": result["final_url"],
                        "record_type": "contract2",
                        "data": {
                            "title": "isolated",
                            "authorization": request["headers"].get("Authorization"),
                            "request_body_present": "body_b64" in request,
                        },
                        "evidence": {"title": {"method": "fixture"}},
                    }], "requests": []}
                if operation == "exporter.export":
                    return {"run_id": payload["run_id"], "format": payload["options"].get("format")}
                if operation == "hook.before_fetch":
                    return {"payload": payload}
                return {}
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


def _result() -> FetchResult:
    request = CrawlRequest(
        "https://example.com/",
        headers={"Authorization": "Bearer secret", "Accept": "text/html"},
        body=b"password=secret",
    )
    return FetchResult(
        request=request,
        final_url=request.url,
        status=200,
        headers={"content-type": "text/html", "set-cookie": "session=secret"},
        body=b"<title>isolated</title>",
        elapsed_seconds=0.1,
    )


def test_c2_processor_adapter_returns_process_result(c2_extension_plugin: Path) -> None:
    host = _SubprocessSessionHost(
        c2_extension_plugin, "c2_extensions", permissions=set(), timeout_seconds=15
    )
    adapter = SubprocessProcessorAdapter(host, options={"mode": "fixture"})
    outcome = adapter.process(_result())
    assert outcome.records[0].record_type == "contract2"
    assert outcome.records[0].data == {
        "title": "isolated",
        "authorization": "<redacted>",
        "request_body_present": False,
    }
    adapter.close()


def test_c2_exporter_adapter_binds_run_context(c2_extension_plugin: Path) -> None:
    host = _SubprocessSessionHost(
        c2_extension_plugin, "c2_extensions", permissions=set(), timeout_seconds=15
    )
    adapter = SubprocessExporterAdapter(host)
    assert adapter(None, object(), "run-1", {"format": "json"}) == {
        "run_id": "run-1",
        "format": "json",
    }
    assert host._run_id == "run-1"
    adapter.close()


def test_c2_hook_adapter_redacts_credentials_and_host_objects(c2_extension_plugin: Path) -> None:
    host = _SubprocessSessionHost(
        c2_extension_plugin, "c2_extensions", permissions=set(), timeout_seconds=15
    )
    callback = SubprocessHookAdapter(host).callback("before_fetch")
    response = callback(run_id="run-2", request=_result().request, pipeline=object())
    payload = response["payload"]
    assert payload["request"]["headers"]["Authorization"] == "<redacted>"
    assert payload["request"]["headers"]["Accept"] == "text/html"
    assert "body_b64" not in payload["request"]
    assert payload["pipeline"] == {"type": "object"}
    host.close()


@pytest.fixture()
def c2_files_source_plugin(tmp_path: Path) -> Path:
    """契约 2 文件型 source：回显收到的 file_path/files/workspace。"""
    (tmp_path / "c2_files.py").write_text(
        textwrap.dedent(
            """
            def handle(operation, payload):
                if operation == "source.seed":
                    return {"requests": [{
                        "url": "https://example.com/file-seed", "method": "GET", "kind": "page",
                        "meta": {
                            "file_path": payload.get("file_path", ""),
                            "files": list(payload.get("files", [])),
                            "workspace": payload.get("workspace", ""),
                        },
                    }]}
                return {}
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


def _files_source_config(root: Path, source: dict) -> object:
    """按 #75 契约构造 config：项目根 + 工作区 + source 段。"""
    from omnicrawler.core.config import AppConfig

    workspace = root / "work"
    workspace.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        root / "task.yaml",
        root,
        {"project": {"name": "t", "workspace": "work"}, "source": source},
        workspace,
    )


def test_c2_source_adapter_injects_input_file_and_workspace(
    c2_files_source_plugin: Path, tmp_path: Path
) -> None:
    """#75：文件型插件源需要在 seed 载荷里拿到**工作区内绝对路径**的入口。

    契约在 PR 复审时收紧：原实现透传相对路径（`"savedrecs.xls"`），插件必须自己再解析
    一次工作区；现在宿主解析并限定后注入，插件侧的工作区回落分支不再有机会被触发。
    """
    root = tmp_path / "proj"
    root.mkdir()
    config = _files_source_config(
        root, {"kind": "c2_files", "file": "savedrecs.xls", "files": ["a.csv", "b.ris"]}
    )
    workspace = root / "work"
    for name in ("savedrecs.xls", "a.csv", "b.ris"):
        (workspace / name).write_text("x", encoding="utf-8")
    host = _SubprocessSessionHost(
        c2_files_source_plugin,
        "c2_files",
        permissions={"files:read"},
        input_files=("*.xls", "*.csv", "*.ris"),
        config=config,
        timeout_seconds=15,
    )
    adapter = SubprocessSourceAdapter(host, config)
    requests = adapter.seed()
    assert len(requests) == 1
    meta = requests[0].meta
    assert meta["file_path"] == str((workspace / "savedrecs.xls").resolve())
    assert meta["files"] == [
        str((workspace / "a.csv").resolve()),
        str((workspace / "b.ris").resolve()),
    ]
    assert meta["workspace"] == str(workspace)
    adapter.close()


def test_c2_source_adapter_rejects_entry_escaping_workspace(
    c2_files_source_plugin: Path, tmp_path: Path
) -> None:
    """越界入口必须拒绝：静默回落成「工作区 + 文件名」会让配置错误看起来像成功。"""
    root = tmp_path / "proj"
    root.mkdir()
    config = _files_source_config(root, {"kind": "c2_files", "file": "../../outside.xls"})
    host = _SubprocessSessionHost(
        c2_files_source_plugin, "c2_files", permissions=set(), config=config, timeout_seconds=15
    )
    with pytest.raises(PolicyBlockedError, match="escapes the workspace"):
        SubprocessSourceAdapter(host, config).seed()


def test_c2_source_adapter_rejects_entry_outside_declared_input_files(
    c2_files_source_plugin: Path, tmp_path: Path
) -> None:
    """manifest 声明了 input_files 时，未命中白名单的入口必须拒绝。"""
    root = tmp_path / "proj"
    root.mkdir()
    config = _files_source_config(root, {"kind": "c2_files", "file": "savedrecs.xls"})
    (root / "work").mkdir(exist_ok=True)
    host = _SubprocessSessionHost(
        c2_files_source_plugin,
        "c2_files",
        permissions=set(),
        input_files=("*.csv",),
        config=config,
        timeout_seconds=15,
    )
    with pytest.raises(PolicyBlockedError, match="input_files"):
        SubprocessSourceAdapter(host, config).seed()


def test_c2_source_adapter_rejects_declared_input_files_without_entry(
    c2_files_source_plugin: Path, tmp_path: Path
) -> None:
    """声明了 input_files 却一个入口都没配 ⇒ 显式失败，而不是发出空载荷。"""
    root = tmp_path / "proj"
    root.mkdir()
    config = _files_source_config(root, {"kind": "c2_files"})
    host = _SubprocessSessionHost(
        c2_files_source_plugin,
        "c2_files",
        permissions=set(),
        input_files=("*.xls",),
        config=config,
        timeout_seconds=15,
    )
    with pytest.raises(PolicyBlockedError, match="no source.file"):
        SubprocessSourceAdapter(host, config).seed()


def test_c2_source_adapter_rejects_entries_without_files_read_permission(
    c2_files_source_plugin: Path, tmp_path: Path
) -> None:
    """没声明 files:read 的插件不交付入口：能力范围必须与声明的权限一致。"""
    root = tmp_path / "proj"
    root.mkdir()
    config = _files_source_config(root, {"kind": "c2_files", "file": "savedrecs.xls"})
    host = _SubprocessSessionHost(
        c2_files_source_plugin,
        "c2_files",
        permissions=set(),
        input_files=("*.xls",),
        config=config,
        timeout_seconds=15,
    )
    with pytest.raises(PolicyBlockedError, match="files:read"):
        SubprocessSourceAdapter(host, config).seed()
