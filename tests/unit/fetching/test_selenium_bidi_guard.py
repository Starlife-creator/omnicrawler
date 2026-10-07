"""S2.5.12：Selenium BiDi guard 默认可用 + 出口异常失败关闭。"""

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


def test_navigation_completes_through_bidi_before_classic_reads(tmp_path, monkeypatch):
    webdriver = pytest.importorskip("selenium.webdriver")
    from omnicrawler.core.models import CrawlRequest

    events = []

    class Driver:
        service = SimpleNamespace(process=SimpleNamespace(pid=2147483647), stop=lambda: None)
        current_window_handle = "active-account-context"
        command_executor = SimpleNamespace(client_config=SimpleNamespace(websocket_timeout=30))

        def __init__(self):
            self.browsing_context = SimpleNamespace(navigate=self.navigate)
            self.script = SimpleNamespace(evaluate=self.evaluate)

        def evaluate(self, **kwargs):
            assert events[-1] == "ready"
            assert kwargs["target"] == {"context": self.current_window_handle}
            return {"type": "success", "result": {"type": "string", "value": '["https://example.org/", "verified", "complete"]'}}

        def navigate(self, **kwargs):
            assert events == ["guard"]
            assert self.command_executor.client_config.websocket_timeout == 5
            assert kwargs == {"context": self.current_window_handle,
                              "url": "https://example.org/", "wait": "complete"}
            events.append("ready")

        def get(self, _url):
            pytest.fail("classic navigation can block the BiDi continue command")

        def set_page_load_timeout(self, _timeout):
            pass

        @property
        def page_source(self):
            pytest.fail("classic source read can block the BiDi continue command")

        current_url = "https://example.org/"

        def quit(self):
            events.append("closed")

    monkeypatch.setattr(webdriver, "Chrome", lambda **_kwargs: Driver())
    config = load_config(_config(tmp_path))
    config.raw["http"].update(timeout_seconds=5, selenium_watchdog_seconds=10)
    fetcher = BrowserFetcher(config)
    fetcher.egress = SimpleNamespace(authorize=lambda *_a, **_k: None, record_response=lambda *_a, **_k: None)
    monkeypatch.setattr(fetcher, "_install_selenium_guard", lambda *_a, **_k: events.append("guard"))
    result = fetcher._selenium(CrawlRequest("https://example.org/"))
    assert result.body == b"verified" and events == ["guard", "ready", "closed"]


def test_guard_subscription_is_already_inside_owned_driver_watchdog(tmp_path, monkeypatch):
    import threading

    webdriver = pytest.importorskip("selenium.webdriver")

    from omnicrawler.core.models import CrawlRequest
    from omnicrawler.fetching.browser_fetcher import SeleniumRuntimeUnavailableError

    stopped = threading.Event()
    driver = SimpleNamespace(service=SimpleNamespace(process=SimpleNamespace(pid=2147483647), stop=stopped.set),
                             quit=lambda: None)
    monkeypatch.setattr(webdriver, "Chrome", lambda **_kwargs: driver)
    config = load_config(_config(tmp_path))
    config.raw["http"]["selenium_watchdog_seconds"] = 1
    fetcher = BrowserFetcher(config)
    def hung_guard(*_args, **_kwargs):
        assert stopped.wait(3), "guard setup was outside the watchdog"
        raise RuntimeError("owned driver interrupted")
    monkeypatch.setattr(fetcher, "_install_selenium_guard", hung_guard)
    with pytest.raises(SeleniumRuntimeUnavailableError):
        fetcher._selenium(CrawlRequest("https://example.org/"))
    assert stopped.is_set()


def test_persistent_startup_failure_never_retries_with_blank_account(tmp_path, monkeypatch):
    webdriver = pytest.importorskip("selenium.webdriver")
    from omnicrawler.core.models import CrawlRequest

    calls = []
    def unavailable(**kwargs):
        calls.append(list(kwargs["options"].arguments))
        raise RuntimeError("profile is occupied")
    monkeypatch.setattr(webdriver, "Chrome", unavailable)
    config = load_config(_config(tmp_path))
    config.raw["browser"]["persist_profile"] = True
    fetcher = BrowserFetcher(config)
    with pytest.raises(RuntimeError, match="未切换到空白会话"):
        fetcher._selenium(CrawlRequest("https://example.org/"))
    assert len(calls) == 1 and any(argument.startswith("--user-data-dir=") for argument in calls[0])


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


def test_bidi_guard_non_permission_error_blocks_request(tmp_path: Path, monkeypatch) -> None:
    fetcher = BrowserFetcher(load_config(_config(tmp_path)))

    def _boom(*_a, **_k):
        raise KeyError("unexpected")  # 出口未授权完成不能放行

    fetcher.egress = SimpleNamespace(authorize=_boom)
    network = _FakeNetwork()
    fetcher._install_selenium_guard(SimpleNamespace(network=network))
    request = _FakeRequest()
    network.handler(request)  # 不抛异常，但明确阻止
    assert request.failed is True
    assert request.continued == 0


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


def test_optional_fallback_requires_public_task_and_records_effective_renderer(tmp_path, monkeypatch):
    from omnicrawler.core.models import CrawlRequest, FetchResult
    from omnicrawler.fetching.browser_fetcher import SeleniumRuntimeUnavailableError
    config = load_config(_config(tmp_path))
    config.raw["browser"].update(engine="selenium", selenium_fallback_engine="playwright")
    fetcher = BrowserFetcher(config)
    request = CrawlRequest("https://example.org/x", render=True)
    monkeypatch.setattr(fetcher, "_selenium", lambda _request: (_ for _ in ()).throw(SeleniumRuntimeUnavailableError("test")))
    monkeypatch.setattr(fetcher, "_playwright", lambda req: FetchResult(req, req.url, 200, {}, b"verified", 0.1))
    monkeypatch.setattr(fetcher.egress.policy, "require", lambda *_args, **_kwargs: None)
    result = fetcher.fetch(request)
    assert result.meta["renderer_fallback"]["effective"] == "playwright"
    config.raw["session"]["persist_cookies"] = True
    with pytest.raises(SeleniumRuntimeUnavailableError):
        fetcher.fetch(request)
