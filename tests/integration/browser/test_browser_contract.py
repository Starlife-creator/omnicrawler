from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.fetching.browser_fetcher import BrowserFetcher, PlaywrightPool
from omnicrawl.security.policy import NetworkTargetPolicy


def _config(tmp_path: Path, *, persist=False, capture_limit=1024):
    value = {
        "project": {"name": "browser-test", "workspace": str(tmp_path / "workspace")},
        "source": {"kind": "browser", "seeds": ["https://example.org"]},
        "http": {
            "resolve_dns": False,
            "respect_robots": False,
            "delay_seconds": 0,
            "headers": {"X-Global": "yes"},
        },
        "browser": {
            "engine": "playwright",
            "headless": True,
            "capture_api_responses": True,
            "max_api_response_bytes": capture_limit,
            "max_api_capture_bytes": capture_limit * 2,
        },
        "session": {"persist_cookies": persist, "name": "account"},
    }
    path = tmp_path / "browser.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return load_config(path)


class _Locator:
    def __init__(self, count=1):
        self._count = count
        self.calls = []

    def count(self):
        return self._count

    def wait_for(self, **kwargs):
        self.calls.append(("wait_for", kwargs))

    def click(self, **kwargs):
        self.calls.append(("click", kwargs))

    def fill(self, value):
        self.calls.append(("fill", value))

    def press(self, value):
        self.calls.append(("press", value))

    def select_option(self, value):
        self.calls.append(("select_option", value))

    def check(self):
        self.calls.append(("check", None))


class _Page:
    def __init__(self):
        self.locators = {"#ok": _Locator(), "#missing": _Locator(0), "#fallback": _Locator()}
        self.calls = []

    def locator(self, selector):
        return self.locators.setdefault(selector, _Locator(0))

    def get_by_role(self, role, name=None):
        self.calls.append(("role", role, name))
        return self.locators["#ok"]

    def wait_for_url(self, value, timeout):
        self.calls.append(("wait_for_url", value, timeout))

    def evaluate(self, script):
        self.calls.append(("evaluate", script))

    def wait_for_timeout(self, value):
        self.calls.append(("wait_for_timeout", value))


def test_playwright_action_contract_all_actions_optional_and_errors() -> None:
    page = _Page()
    actions = [
        {"action": "wait_for", "selector": "#ok", "timeout_ms": 20},
        {"action": "wait_for_url", "value": "**/done", "timeout_ms": 30},
        {"action": "click", "selectors": ["#missing", "#fallback"]},
        {"action": "fill", "role": "textbox", "name": "Search", "value": "policy"},
        {"action": "press", "selector": "#ok", "key": "Enter"},
        {"action": "select_option", "selector": "#ok", "value": "two"},
        {"action": "check", "selector": "#ok"},
        {"action": "scroll_bottom", "times": 2, "pause_ms": 0},
        {"action": "wait_ms", "value": 12},
        {"action": "manual_pause", "timeout_ms": 13},
        {"action": "click", "selector": "#missing", "if_present": True},
        {"action": "unknown", "optional": True},
    ]
    BrowserFetcher._run_actions(page, actions)
    assert ("click", {"timeout": 10000}) in page.locators["#fallback"].calls
    assert ("fill", "policy") in page.locators["#ok"].calls
    assert len([item for item in page.calls if item[0] == "evaluate"]) == 2
    assert ("wait_for_timeout", 12) in page.calls
    assert ("wait_for_timeout", 13) in page.calls

    assert BrowserFetcher._action_locator(page, {"selectors": ["#missing", "#fallback"]}) is page.locators["#fallback"]
    assert BrowserFetcher._action_locator(page, {}) is None
    with pytest.raises(RuntimeError, match="第 1 步"):
        BrowserFetcher._run_actions(page, [{"action": "click"}])
    with pytest.raises(RuntimeError, match="不支持"):
        BrowserFetcher._run_actions(page, [{"action": "unknown"}])


class _Element:
    def __init__(self, name="Target", selected=False):
        self.accessible_name = name
        self.text = name
        self.selected = selected
        self.calls = []

    def click(self):
        self.calls.append(("click", None))
        self.selected = True

    def clear(self):
        self.calls.append(("clear", None))

    def send_keys(self, value):
        self.calls.append(("send_keys", value))

    def is_selected(self):
        return self.selected


class _Driver:
    def __init__(self):
        self.current_url = "https://example.org/done"
        self.element = _Element()
        self.scripts = []

    def find_elements(self, _by, selector):
        return [] if selector == "#missing" else [self.element]

    def execute_script(self, script):
        self.scripts.append(script)


def test_selenium_action_contract_all_actions(monkeypatch) -> None:
    support_ui = pytest.importorskip("selenium.webdriver.support.ui")

    class Wait:
        def __init__(self, driver, timeout):
            self.driver = driver
            self.timeout = timeout

        def until(self, callback):
            value = callback(self.driver)
            if not value:
                raise TimeoutError("condition failed")
            return value

    selected = []

    class Select:
        def __init__(self, element):
            self.element = element

        def select_by_value(self, value):
            selected.append(value)

    monkeypatch.setattr(support_ui, "WebDriverWait", Wait)
    monkeypatch.setattr(support_ui, "Select", Select)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    driver = _Driver()
    BrowserFetcher._run_selenium_actions(
        driver,
        [
            {"action": "wait_for", "selector": "#ok"},
            {"action": "wait_for_url", "value": "**/done"},
            {"action": "click", "selector": "#ok"},
            {"action": "fill", "selector": "#ok", "value": "policy"},
            {"action": "press", "selector": "#ok", "key": "Enter"},
            {"action": "select_option", "selector": "#ok", "value": "two"},
            {"action": "check", "selector": "#ok"},
            {"action": "scroll_bottom", "times": 2, "pause_ms": 1},
            {"action": "wait_ms", "value": 5},
            {"action": "manual_pause", "timeout_ms": 6},
            {"action": "click", "selector": "#missing", "if_present": True},
            {"action": "unknown", "optional": True},
        ],
    )
    assert selected == ["two"]
    assert len(driver.scripts) == 2
    assert ("clear", None) in driver.element.calls
    assert any(call[0] == "send_keys" for call in driver.element.calls)

    with pytest.raises(RuntimeError, match="浏览器动作第 1 步失败"):
        BrowserFetcher._run_selenium_actions(driver, [{"action": "unknown"}])


class _Context:
    def __init__(self):
        self.headers = None
        self.storage_path = None
        self.closed = False

    def set_extra_http_headers(self, headers):
        self.headers = headers

    def storage_state(self, path):
        self.storage_path = Path(path)
        self.storage_path.write_text("{}", encoding="utf-8")

    def close(self):
        self.closed = True


class _Browser:
    def __init__(self):
        self.options = None
        self.context = _Context()

    def new_context(self, **options):
        self.options = options
        return self.context


def _pool(config):
    pool = object.__new__(PlaywrightPool)
    pool.config = config
    pool.target_policy = NetworkTargetPolicy(config)
    return pool


def test_pool_context_state_headers_proxy_and_route_guard(tmp_path: Path) -> None:
    config = _config(tmp_path, persist=True)
    pool = _pool(config)
    request = CrawlRequest(
        "https://example.org",
        headers={"X-Request": "yes"},
        meta={"account": "user/name", "proxy": "http://proxy.example:8080"},
    )
    key = pool._context_key(request)
    assert key == "user/name|http://proxy.example:8080"
    state_path = pool._state_path(key)
    assert state_path is not None and "user_name_http___proxy" in state_path.name
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")

    browser = _Browser()
    context = pool._new_context(browser, key, request)
    assert browser.options["storage_state"] == str(state_path)
    assert browser.options["proxy"] == {"server": "http://proxy.example:8080"}
    assert context.headers == {"X-Global": "yes", "X-Request": "yes"}

    with patch("os.chmod", side_effect=OSError("unsupported")):
        pool._save_context(context, key)
    assert context.storage_path == state_path

    transient = _pool(_config(tmp_path, persist=False))
    assert transient._state_path("anything") is None
    transient._save_context(context, "anything")

    allowed = SimpleNamespace(
        request=SimpleNamespace(url="https://example.org"),
        continue_=MagicMock(),
        abort=MagicMock(),
    )
    pool._guard_route(allowed)
    allowed.continue_.assert_called_once()
    blocked = SimpleNamespace(
        request=SimpleNamespace(url="http://127.0.0.1/private"),
        continue_=MagicMock(),
        abort=MagicMock(),
    )
    pool._guard_route(blocked)
    blocked.abort.assert_called_once_with("blockedbyclient")


class _RequestInfo:
    def __init__(self, method="POST", post_data='{"page": 2}', resource_type="xhr"):
        self.method = method
        self.post_data = post_data
        self.resource_type = resource_type
        self.headers = {
            "Accept": "application/json",
            "Authorization": "Bearer secret",
            "Referer": "https://example.org",
        }


class _ResponseInfo:
    def __init__(self, body=b'{"items": [1]}', content_type="application/json", request=None):
        self.url = "https://example.org/api/items"
        self.status = 200
        self.headers = {"content-type": content_type}
        self.request = request or _RequestInfo()
        self._body = body

    def body(self):
        return self._body


def test_api_capture_json_text_size_limits_and_redaction(tmp_path: Path) -> None:
    pool = _pool(_config(tmp_path, capture_limit=64))
    output = []
    pool._capture_response(_ResponseInfo(), output)
    assert output[0]["json"] == {"items": [1]}
    assert output[0]["request_payload"] == {"page": 2}
    assert "Authorization" not in output[0]["request_headers"]

    pool._capture_response(
        _ResponseInfo(b"plain", "text/plain", _RequestInfo(method="GET", resource_type="fetch")),
        output,
    )
    assert output[1]["text"] == "plain"

    pool._capture_response(_ResponseInfo(b"x" * 100), output)
    assert output[2]["capture_skipped"] == "size_limit"

    invalid = _ResponseInfo(b"not-json")
    invalid.request = _RequestInfo(post_data="raw-payload")
    pool._capture_response(invalid, output)
    assert output[3]["text"] == "not-json"
    assert output[3]["request_payload"] == "raw-payload"

    ignored = _ResponseInfo(b"html", "text/html", _RequestInfo(method="GET", resource_type="document"))
    pool._capture_response(ignored, output)
    assert len(output) == 4


def test_browser_fetcher_close_invalid_engine_and_playwright_pool(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    limiter = SimpleNamespace(wait=MagicMock())
    fetcher = BrowserFetcher(config, limiter=limiter)
    fake_pool = SimpleNamespace(close=MagicMock(), fetch=MagicMock())
    fetcher._playwright_pool = fake_pool
    fetcher.close()
    fake_pool.close.assert_called_once()
    fetcher.close()

    config.raw["browser"]["engine"] = "invalid"
    with pytest.raises(ValueError, match="browser.engine"):
        fetcher.fetch(CrawlRequest("https://example.org"))
    limiter.wait.assert_called()

    pytest.importorskip("playwright.sync_api")
    config.raw["browser"]["engine"] = "playwright"
    expected = FetchResult(
        CrawlRequest("https://example.org"),
        "https://example.org",
        200,
        {"content-type": "text/html"},
        b"<html/>",
        0.1,
    )
    pool_instance = SimpleNamespace(fetch=MagicMock(return_value=expected), close=MagicMock())
    monkeypatch.setattr("omnicrawl.browser_fetcher.PlaywrightPool", lambda *_args: pool_instance)
    fetcher._playwright_pool = None
    result = fetcher._playwright(CrawlRequest("https://example.org"))
    assert result is expected
