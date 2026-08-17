"""S2.5.13：browser 配置代理 context 键修复。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest
from omnicrawler.fetching.browser_fetcher import PlaywrightPool


def _config(tmp_path: Path, *, proxy: str = "") -> Path:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s2513, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        f"http: {{proxy: {proxy!r}}}\n",
        encoding="utf-8",
    )
    return config_path


def _pool(config_path: Path) -> PlaywrightPool:
    pool = object.__new__(PlaywrightPool)
    pool.config = load_config(config_path)
    return pool


def test_new_context_uses_config_proxy(tmp_path: Path) -> None:
    pool = _pool(_config(tmp_path, proxy="http://user:pass@proxy.example:8080"))
    calls: dict = {}

    class _Browser:
        def new_context(self, **options):
            calls.update(options)
            return SimpleNamespace()

    request = CrawlRequest("https://example.org/")
    pool._new_context(_Browser(), pool._context_key(request), request)
    assert calls["proxy"] == {"server": "http://user:pass@proxy.example:8080"}


def test_new_context_meta_proxy_overrides_config(tmp_path: Path) -> None:
    pool = _pool(_config(tmp_path, proxy="http://config-proxy.example:8080"))
    calls: dict = {}

    class _Browser:
        def new_context(self, **options):
            calls.update(options)
            return SimpleNamespace()

    request = CrawlRequest(
        "https://example.org/", meta={"proxy": "http://meta-proxy.example:8080"}
    )
    pool._new_context(_Browser(), pool._context_key(request), request)
    assert calls["proxy"] == {"server": "http://meta-proxy.example:8080"}


def test_context_key_distinguishes_config_proxy(tmp_path: Path) -> None:
    pool = _pool(_config(tmp_path, proxy="http://proxy.example:8080"))
    request = CrawlRequest("https://example.org/")
    assert pool._context_key(request) == "default|http://proxy.example:8080"


def test_new_context_without_proxy_sets_none(tmp_path: Path) -> None:
    pool = _pool(_config(tmp_path))
    calls: dict = {}

    class _Browser:
        def new_context(self, **options):
            calls.update(options)
            return SimpleNamespace()

    request = CrawlRequest("https://example.org/")
    pool._new_context(_Browser(), pool._context_key(request), request)
    assert "proxy" not in calls
