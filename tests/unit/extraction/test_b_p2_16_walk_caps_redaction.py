"""B-P2-16：_walk 深度 / 节点总数上限防 RecursionError；构造早期即脱敏，
密钥不进入下游 profile 内存结构。
"""

from __future__ import annotations

from omnicrawler.extraction.api_discovery import (
    _WALK_MAX_DEPTH,
    _WALK_MAX_NODES,
    _walk,
    discover_api_endpoints,
)


def test_walk_respects_depth_cap_on_deep_nesting() -> None:
    # 深度远超 _WALK_MAX_DEPTH 的嵌套字典：递归必须在深度上限处停止，不抛 RecursionError。
    payload: object = {"leaf": 1}
    for _ in range(_WALK_MAX_DEPTH + 40):
        payload = {"child": payload}

    walked = list(_walk(payload))
    assert walked, "上限内至少应产出根节点"
    max_depth = max(path.count(".") for path, _ in walked)
    assert max_depth <= _WALK_MAX_DEPTH


def test_walk_respects_node_total_cap() -> None:
    payload = {f"key_{index}": index for index in range(_WALK_MAX_NODES + 500)}
    walked = list(_walk(payload))
    assert len(walked) <= _WALK_MAX_NODES + 1  # 根节点 + 受上限约束的子节点


def test_secret_redacted_at_profile_construction() -> None:
    """密钥在 profile 对象构造早期即脱敏，profile.request_payload 本身不含明文。"""
    responses = [{
        "url": "https://api.example.org/v1/items",
        "method": "POST",
        "status": 200,
        "content_type": "application/json",
        "request_payload": {
            "username": "alice",
            "password": "hunter2",
            "api_key": "AKIA12345",
            "filter": {"token": "c0ffee"},
        },
        "json": {"results": [{"id": 1, "name": "A"}]},
    }]
    profiles = discover_api_endpoints(responses)
    assert len(profiles) == 1
    payload = profiles[0].request_payload
    # 明文密钥不得出现在 profile 内存结构中
    flat = repr(payload)
    assert "hunter2" not in flat
    assert "AKIA12345" not in flat
    assert "c0ffee" not in flat
    assert payload["password"] == "***"
    assert payload["api_key"] == "***"
    assert payload["filter"]["token"] == "***"
    # 非敏感字段保留
    assert payload["username"] == "alice"
