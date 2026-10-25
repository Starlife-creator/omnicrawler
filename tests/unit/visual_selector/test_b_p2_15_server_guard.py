"""B-P2-15：本地选择器服务守卫回归——Origin 白名单、连接状态按 client 隔离、事件列表有界。

浏览器真实 Origin 握手行为需 GUI/浏览器环境验证；本测试覆盖纯逻辑层守卫。
"""

from __future__ import annotations

import json

from omnicrawler.visual_selector.server import VisualSelectorServer, _MAX_SELECTIONS


class _FakeWebSocket:
    async def send(self, *_args: object, **_kwargs: object) -> None:
        return None


def test_origin_whitelist_allows_loopback_and_non_browser() -> None:
    assert VisualSelectorServer._origin_allowed(None)
    assert VisualSelectorServer._origin_allowed("http://localhost:8084")
    assert VisualSelectorServer._origin_allowed("http://127.0.0.1:8084")
    assert not VisualSelectorServer._origin_allowed("https://evil.example.com")


def test_connection_state_is_per_client_isolated() -> None:
    server = VisualSelectorServer()
    assert not server.connected
    server._connected_clients["c1"] = True
    server._connected_clients["c2"] = True
    assert server.connected
    server._connected_clients.pop("c1")
    assert server.connected
    server._connected_clients.pop("c2")
    assert not server.connected


async def test_selections_list_is_bounded() -> None:
    server = VisualSelectorServer()
    websocket = _FakeWebSocket()
    for index in range(_MAX_SELECTIONS + 1):
        await server._dispatch(
            {"type": 3, "message": {"pipe": json.dumps(
                {"xpath": "//div", "content": f"x{index}"})}},
            websocket,
        )
    selections = server.get_selections()
    assert len(selections) == _MAX_SELECTIONS
    # 最旧事件被淘汰，最新事件保留
    assert selections[-1].content == f"x{_MAX_SELECTIONS}"


async def test_registration_message_propagates_seed_url() -> None:
    server = VisualSelectorServer()
    await server._dispatch(
        {"type": 0, "message": {"title": "页面", "url": "https://example.org/page"}},
        _FakeWebSocket(),
    )
    assert server.converter._seed_url == "https://example.org/page"
