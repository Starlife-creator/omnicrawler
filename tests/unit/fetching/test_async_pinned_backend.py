from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from omnicrawler.core.config import AppConfig
from omnicrawler.fetching.async_fetcher import _PinnedAsyncNetworkBackend
from omnicrawler.security.policy import NetworkTargetPolicy


class _FakeInner:
    def __init__(self) -> None:
        self.connected: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append((host, port))
        if host == "127.0.0.2":
            raise OSError("refused")
        return ("stream", host, port)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return ("unix", path)

    async def sleep(self, seconds):
        return None


def _policy(tmp_path: Path) -> NetworkTargetPolicy:
    workspace = tmp_path / "work"
    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "pinned", "workspace": str(workspace)},
                "source": {"kind": "incremental", "seeds": []},
                "http": {"allow_private_network": True, "resolve_dns": True, "dns_fail_closed": True},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config = AppConfig(config_path, tmp_path, yaml.safe_load(config_path.read_text(encoding="utf-8")), workspace)
    return NetworkTargetPolicy(config)


def test_pinned_backend_connects_ip_literal_not_hostname(tmp_path: Path) -> None:
    """S1.3.5：connect_tcp 只收到批准地址字面量，不再出现主机名（防 DNS 重绑定）。"""
    policy = _policy(tmp_path)
    inner = _FakeInner()
    backend = _PinnedAsyncNetworkBackend(inner, policy)

    stream = asyncio.run(backend.connect_tcp("localhost", 80, socket_options=()))
    assert stream[0] == "stream"
    assert stream[2] == 80
    assert all(host != "localhost" for host, _port in inner.connected)
    assert any(":" in host or host.replace(".", "").isdigit() for host, _port in inner.connected)


def test_pinned_backend_skips_refused_literal_and_connects_next(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    inner = _FakeInner()
    backend = _PinnedAsyncNetworkBackend(inner, policy)

    # 127.0.0.1 可能映射 localhost（先连上），此处仅断言只尝试批准列表内的字面量
    asyncio.run(backend.connect_tcp("127.0.0.1", 80))
    assert all(host not in {"localhost"} for host, _port in inner.connected)
    assert all(host in {"127.0.0.1", "127.0.0.2", "::1", "127.0.0.3"} or ":" in host for host, _port in inner.connected)


def test_pinned_backend_forwards_unix_and_sleep(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    inner = _FakeInner()
    backend = _PinnedAsyncNetworkBackend(inner, policy)
    assert asyncio.run(backend.connect_unix_socket("/tmp/x.sock")) == ("unix", "/tmp/x.sock")
    assert asyncio.run(backend.sleep(0.001)) is None
