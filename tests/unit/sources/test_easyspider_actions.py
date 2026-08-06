"""S1.5.7 消费方测试：EasySpider scroll 动作支持 + 点击 wait 语义修正。

验证：scrollCount>1 的导入任务不再产出无法执行的 action；点击后等待
产出显式 wait_ms（而非被误用作元素查找超时）。
"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawl.sources.easyspider_bridge import EasySpiderImporter


def _write_task(tmp_path: Path, graph: list[dict]) -> Path:
    task = {
        "name": "scroll_task",
        "url": "https://example.com/list",
        "version": "1.0",
        "graph": graph,
    }
    path = tmp_path / "task.json"
    path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    return path


def _op_node(node_id: int, parent_id: int, option: int, params: dict, title: str) -> dict:
    return {
        "id": node_id, "parentId": parent_id, "type": 0, "option": option,
        "parameters": params, "title": title, "position": node_id,
    }


def test_s157_scroll_count_greater_than_one_imports_scroll_action(tmp_path: Path) -> None:
    """S1.5.7：scrollType!=0 + scrollCount>1 产出 scroll 动作，不产出非法 action。"""
    path = _write_task(tmp_path, [
        _op_node(0, 0, 1, {"url": "https://example.com/list"}, "打开网页"),
        _op_node(1, 0, 5, {"scrollType": 1, "scrollCount": 3}, "滚动"),
    ])
    importer = EasySpiderImporter(path)
    config = importer.to_config()
    actions = config["browser"]["actions"]
    assert {"action": "scroll", "value": "3"} in actions


def test_s157_click_wait_becomes_explicit_wait_ms(tmp_path: Path) -> None:
    """S1.5.7：点击后等待产出 wait_ms 动作（不再误用作元素查找超时）。"""
    path = _write_task(tmp_path, [
        _op_node(0, 0, 1, {"url": "https://example.com/page"}, "打开网页"),
        _op_node(1, 0, 2, {"xpath": "//button[1]", "wait": 5}, "点击元素"),
    ])
    importer = EasySpiderImporter(path)
    config = importer.to_config()
    actions = config["browser"]["actions"]
    assert {"action": "click", "selector": "//button[1]"} in actions
    assert {"action": "wait_ms", "value": 5000} in actions


def test_s157_scroll_bottom_zero_type_uses_scroll_bottom(tmp_path: Path) -> None:
    """S1.5.7：scrollType=0 仍产出 scroll_bottom。"""
    path = _write_task(tmp_path, [
        _op_node(0, 0, 1, {"url": "https://example.com/page"}, "打开网页"),
        _op_node(1, 0, 5, {"scrollType": 0}, "滚动"),
    ])
    importer = EasySpiderImporter(path)
    config = importer.to_config()
    assert {"action": "scroll_bottom"} in config["browser"]["actions"]


def test_s157_scroll_dispatch_reaches_engine() -> None:
    """S1.5.7：browser_fetcher 的 scroll 动作被分派到 scroll_bottom（times=value）。"""
    from omnicrawl.fetching.browser_fetcher import BrowserAction, _dispatch_action

    class _Engine:
        def __init__(self) -> None:
            self.calls: list[BrowserAction] = []

        def scroll_bottom(self, action: BrowserAction) -> None:
            self.calls.append(action)

    engine = _Engine()
    _dispatch_action(BrowserAction(name="scroll", value="3"), engine)
    assert len(engine.calls) == 1
    assert engine.calls[0].name == "scroll_bottom"
    assert engine.calls[0].times == 3
