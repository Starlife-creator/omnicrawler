from __future__ import annotations

from pathlib import Path

import yaml

from omnicrawler.core.config import AppConfig
from omnicrawler.fetching.browser_fetcher import PlaywrightPool
from omnicrawler.security.policy import NetworkTargetPolicy


class _FakeResponse:
    def __init__(
        self,
        url: str = "https://api.example.org/x",
        headers: dict | None = None,
        body: bytes | None = None,
        status: int = 200,
        method: str = "GET",
        resource_type: str = "xhr",
    ) -> None:
        self.url = url
        self.headers = headers or {}
        self._body = body if body is not None else b'{"a": 1}'
        self.status = status
        self._method = method
        self.resource_type = resource_type

    @property
    def request(self):
        return _FakeRequest(self._method, self.url)

    def body(self):
        return self._body


class _FakeRequest:
    def __init__(self, method: str, url: str) -> None:
        self.method = method
        self.post_data = None
        self.resource_type = "xhr"

    @property
    def headers(self):
        return {"accept": "application/json", "cookie": "secret=1"}


class _FakeEgress:
    def __init__(self) -> None:
        self.recorded = 0

    def record_response(self, size: int, url: str) -> None:
        self.recorded += size


def _pool(tmp_path: Path, **overrides) -> PlaywrightPool:
    workspace = tmp_path / "work"
    config_path = tmp_path / "project.yaml"
    data = {
        "project": {"name": "t", "workspace": str(workspace)},
        "source": {"kind": "incremental", "seeds": []},
        "http": {"allow_private_network": True},
    }
    data.update(overrides)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    config = AppConfig(config_path, tmp_path, yaml.safe_load(config_path.read_text(encoding="utf-8")), workspace)
    return PlaywrightPool(config, NetworkTargetPolicy(config), size=1)


def test_capture_respects_per_response_and_total_limit(tmp_path: Path) -> None:
    """S1.3.6: content-length 声明的超大响应在读取前即被拒止。"""
    fetcher = _pool(tmp_path)
    egress = _FakeEgress()
    fetcher.egress = egress  # type: ignore[assignment]

    output: list[dict] = []
    huge = _FakeResponse(
        headers={"content-type": "application/json", "content-length": "999999999"},
        body=b'{"big": true}',
    )
    fetcher._capture_response(huge, output)
    assert output and output[0].get("capture_skipped") == "size_limit"
    assert egress.recorded == 0  # 未读取 body 也未计入预算


def test_capture_total_budget_checked_before_read(tmp_path: Path) -> None:
    fetcher = _pool(tmp_path, browser={"max_api_capture_bytes": 100})
    output: list[dict] = []

    first = _FakeResponse(body=b'{"only": 1}')
    fetcher._capture_response(first, output)
    assert output[0].get("captured_bytes") == 11

    # 剩余预算不足以容纳声明的大小 → 直接拒止
    second = _FakeResponse(
        headers={"content-type": "application/json", "content-length": "999"},
        body=b'{"second": true}',
    )
    fetcher._capture_response(second, output)
    assert output[1].get("capture_skipped") == "size_limit"


def test_capture_keeps_safe_request_headers_only(tmp_path: Path) -> None:
    """S1.3.4: 捕获的 request_headers 只保留安全子集，不含凭据。"""
    fetcher = _pool(tmp_path)
    output: list[dict] = []
    resp = _FakeResponse(headers={"content-type": "application/json"}, body=b'{"ok": 1}')
    fetcher._capture_response(resp, output)
    headers = output[0]["request_headers"]
    assert headers == {"accept": "application/json"}
    assert "authorization" not in headers
