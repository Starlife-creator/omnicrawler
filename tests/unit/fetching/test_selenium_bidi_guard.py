"""S2.5.12：Selenium BiDi guard 默认可用 + 非权限异常放行。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.fetching.browser_fetcher import BrowserFetcher


def _config(tmp_path: Path, *, extra: dict | None = None) -> Path:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s2512, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    return config_path


class _FakeRequest:
    def __init__(self, url: str = "https://example.org/x") -> None:
        self.url = url
        self.headers = {}
        self.failed = False
        self.continued = 0

    def fail(self) -> None:
        self.failed = True

    def continue_request(self) -> None:
        self.continued += 1


class _FakeNetwork:
    def __init__(self) -> None:
        self.handler = None

    def add_request_handler(self, _event, handler) -> None:
        self.handler = handler


def test_bidi_guard_installed_by_default(tmp_path: Path, monkeypatch) -> None:
    fetcher = BrowserFetcher(load_config(_config(tmp_path)))
    fetcher.egress = SimpleNamespace(authorize=lambda *_a, **_k: None)
    network = _FakeNetwork()
    driver = SimpleNamespace(network=network)
    fetcher._install_selenium_guard(driver)
    assert network.handler is not None


def test_bidi_guard_permission_error_blocks_request(tmp_path: Path, monkeypatch) -> None:
    fetcher = BrowserFetcher(load_config(_config(tmp_path)))

    def _block(*_a, **_k):
        raise PermissionError("blocked")

    fetcher.egress = SimpleNamespace(authorize=_block)
    network = _FakeNetwork()
    fetcher._install_selenium_guard(SimpleNamespace(network=network))
    request = _FakeRequest()
    network.handler(request)
    assert request.failed is True
    assert request.continued == 0


def test_bidi_guard_non_permission_error_passes_through(tmp_path: Path, monkeypatch) -> None:
    fetcher = BrowserFetcher(load_config(_config(tmp_path)))

    def _boom(*_a, **_k):
        raise KeyError("unexpected")  # 非 PermissionError 家族：放行而非挂死

    fetcher.egress = SimpleNamespace(authorize=_boom)
    network = _FakeNetwork()
    fetcher._install_selenium_guard(SimpleNamespace(network=network))
    request = _FakeRequest()
    network.handler(request)  # 不抛异常（放行而非挂死）
    assert request.failed is False
    assert request.continued == 1


def test_bidi_guard_unavailable_raises_guidance(tmp_path: Path) -> None:
    fetcher = BrowserFetcher(load_config(_config(tmp_path)))
    with pytest.raises(RuntimeError, match="BiDi 逐请求拦截不可用"):
        fetcher._install_selenium_guard(SimpleNamespace(network=None))


def test_explicit_disable_raises_guidance(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path))
    config.raw["egress"]["experimental_selenium_bidi_guard"] = False
    fetcher = BrowserFetcher(config)
    with pytest.raises(RuntimeError, match="已显式关闭"):
        fetcher._install_selenium_guard(SimpleNamespace(network=_FakeNetwork()))


# ── P9-B2（B03-007/008）：opt-out 已废弃，强制拦截 ─────────────────


def test_legacy_optout_is_ignored_and_guard_installed(tmp_path: Path) -> None:
    """allow_unintercepted_selenium=true 不再放行——拦截仍强制安装（fail-closed）。"""
    config = load_config(_config(tmp_path))
    config.raw["egress"]["allow_unintercepted_selenium"] = True
    fetcher = BrowserFetcher(config)
    fetcher.egress = SimpleNamespace(authorize=lambda *_a, **_k: None)
    network = _FakeNetwork()
    fetcher._install_selenium_guard(SimpleNamespace(network=network))
    assert network.handler is not None  # 拦截已安装，而非 return 跳过
