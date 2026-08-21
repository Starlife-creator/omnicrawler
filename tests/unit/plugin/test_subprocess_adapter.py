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

from omnicrawler.core.models import CrawlRequest
from omnicrawler.plugins.plugin_subprocess_adapter import (
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
