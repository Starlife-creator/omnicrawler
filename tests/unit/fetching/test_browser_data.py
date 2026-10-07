import json

import pytest

from omnicrawler.fetching.browser_data import collect_virtual_items, wait_for_data


def frame(ids, *, ended=False, loading=False):
    return {"items": [{"id": str(value), "text": str(value), "html": f'<article data-id="{value}">{value}</article>'} for value in ids],
            "loading": loading, "ended": ended}


def reader(frames):
    values = iter(frames)
    last = frames[-1]
    def read(expression):
        if "const cfg=" in expression:
            return next(values, last)
        if "container.innerHTML=" in expression:
            markup = json.JSONDecoder().raw_decode(expression.split("container.innerHTML=")[1])[0]
            return f'<div id="list">{markup}</div>'
        return True
    return read


def test_readiness_requires_new_data_and_completed_loading():
    read = reader([frame(["Loading"], loading=True), frame(["Loading"]), frame(["Ready"])])
    result = wait_for_data(read, {"selector": "article", "text_not": "Loading", "stable_ms": 0})
    assert result["state"] == "ready" and result["completeness"] == "unknown"


def test_readiness_timeout_and_cancellation_never_pass():
    with pytest.raises(TimeoutError):
        wait_for_data(reader([frame([])]), {"selector": "article", "timeout_ms": 1, "stable_ms": 0})
    with pytest.raises(InterruptedError):
        wait_for_data(lambda expression: pytest.fail("cancelled wait read DOM"), {"selector": "article"}, should_stop=lambda: True)


def test_virtual_collection_retains_items_evicted_from_dom_and_requires_a_declared_end():
    config = {"item_selector": "article", "container_selector": "#list", "identity_attribute": "data-id", "pause_ms": 1,
              "max_steps": 4, "end_selector": "#end", "stagnant_steps": 2}
    html, status = collect_virtual_items(reader([frame([1, 2]), frame([2, 3], ended=True)]), config, maximum_bytes=10000)
    assert all(f'data-id="{value}"' in html for value in (1, 2, 3))
    assert html.count('data-id="2"') == 1
    assert status["completeness"] == "complete" and status["records_collected"] == 3
    _html, status = collect_virtual_items(reader([frame([1])]), config, maximum_bytes=10000)
    assert status["completeness"] == "partial" and status["stop_reason"] == "no_progress"


def test_empty_declared_end_and_budget_do_not_claim_complete():
    config = {"item_selector": "article", "container_selector": "#list", "identity_attribute": "data-id", "pause_ms": 1,
              "max_steps": 1, "end_selector": "#end", "max_items": 1}
    _, status = collect_virtual_items(reader([frame([], ended=True)]), config, maximum_bytes=10000)
    assert status["completeness"] == "partial"
    _, status = collect_virtual_items(reader([frame([1, 2], ended=True)]), config, maximum_bytes=10000)
    assert status["completeness"] == "partial" and status["stop_reason"] == "collection_budget_exhausted"
