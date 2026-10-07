"""Regression coverage for reads while Chrome navigation is intercepted."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("selenium")
from selenium.common.exceptions import WebDriverException

from omnicrawler.fetching.browser_engines import BrowserAction
from omnicrawler.fetching.selenium_bidi import SeleniumBiDiAdapter


def _driver(locate=None, evaluate=None):
    def forbidden(*_args, **_kwargs):
        pytest.fail("a classic read can prevent the BiDi request from continuing")
    return SimpleNamespace(find_elements=forbidden, execute_script=forbidden,
                           browsing_context=SimpleNamespace(locate_nodes=locate),
                           script=SimpleNamespace(evaluate=evaluate))


@pytest.mark.parametrize("action,expected", [
    (BrowserAction(name="wait_for", selector="#ready"), {"type": "css", "value": "#ready"}),
    (BrowserAction(name="click", role="button", role_name="Continue"),
     {"type": "accessibility", "value": {"role": "button", "name": "Continue"}}),
])
def test_element_wait_uses_bidi_shared_reference(action, expected):
    calls = []
    def locate(**kwargs):
        calls.append(kwargs)
        return [{"sharedId": "owned-element"}]
    driver = _driver(locate=locate)
    adapter = SeleniumBiDiAdapter(driver, "owned-account-context")
    assert adapter.locate(action).id == "owned-element"
    assert calls == [{"context": "owned-account-context", "locator": expected, "max_node_count": 1}]


def test_only_known_navigation_context_transition_is_retried():
    def transition(**_kwargs):
        raise WebDriverException("unknown error: Cannot find context with specified id")
    adapter = SeleniumBiDiAdapter(_driver(locate=transition), "owned-context")
    action = BrowserAction(name="wait_for", selector="#ready")
    assert adapter.locate(action) is None
    def other(**_kwargs):
        raise WebDriverException("invalid selector")
    adapter._driver.browsing_context.locate_nodes = other
    with pytest.raises(WebDriverException, match="invalid selector"):
        adapter.locate(action)


def test_document_wait_retries_transition_and_loading_without_classic_reads():
    attempts = iter([WebDriverException("Cannot find context with specified id"),
                     ["https://example.org/", "incomplete", "loading"],
                     ["https://example.org/final", "verified", "complete"]])
    def evaluate(**kwargs):
        assert kwargs["target"] == {"context": "owned-context"}
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return {"type": "success", "result": {"type": "string", "value": json.dumps(value)}}
    adapter = SeleniumBiDiAdapter(_driver(evaluate=evaluate), "owned-context")
    assert adapter.read_document(2) == ("https://example.org/final", b"verified")


@pytest.mark.parametrize("raw", [None, {"type": "exception"}, {"type": "success", "result": None},
                                 {"type": "success", "result": {"type": "string", "value": '[1, 2, 3]'}}])
def test_invalid_script_results_are_rejected(raw):
    adapter = SeleniumBiDiAdapter(_driver(evaluate=lambda **_kwargs: raw), "owned-context")
    with pytest.raises(RuntimeError, match="BiDi document read"):
        adapter.read_document(0.1)
