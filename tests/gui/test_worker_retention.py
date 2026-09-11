"""守卫：三个曾被误删的 worker 必须保留，且各自带上「测量后的定论」。

## 为什么需要这条守卫

P0-1 首版曾以「**零调用点**」为由删除 `JsonlLoadWorker` / `SqliteQueryWorker` /
`TemplateCombineWorker`（见《审查记录》§6.2 之 6）。那是一次**方法论错误**：
零调用点只说明「还没接线」，不说明「不需要」——这三类各自对应仍开放的文档化需求，
删掉代码等于删掉需求。

2026-09-11 逐项做了测量并定论：

* `JsonlLoadWorker` —— **已接线**（`task_history` 按文件大小分流；实测 10 万行同步解析 214.6 ms）；
* `TemplateCombineWorker` —— **不接线**（实测 76 个模板发现耗时 0.4 ms，低于一帧预算）；
* `SqliteQueryWorker` —— **不接线**（实测 200 次运行 / 10 万事件的查询 0.3–0.7 ms）。

本文件锁住两件事：**类还在**，且**结论写在 docstring 里**（避免下次再被当成死代码）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from omnicrawler.gui.core import workers  # noqa: E402

#: 曾经被误删过的三类。删任何一个都必须先回答 docstring 里那条测量结论。
_RETAINED = ("JsonlLoadWorker", "SqliteQueryWorker", "TemplateCombineWorker")


@pytest.mark.parametrize("name", _RETAINED)
def test_worker_is_still_present(name: str) -> None:
    assert hasattr(workers, name), (
        f"{name} 被移除了。删除前请先论证：《审查记录》§6.2 之 6 记录了"
        "「以零调用点为由删除」是一次方法论错误，且删除条件是三条件同时成立"
        "（能力已被等价替代 ∧ 无文档化需求指向 ∧ 保留成本 > 价值）。"
    )


@pytest.mark.parametrize("name", _RETAINED)
def test_worker_docstring_records_the_measured_decision(name: str) -> None:
    """docstring 必须写明「测量后的定论」，而不是含糊的「当前未接线」。"""
    doc = getattr(workers, name).__doc__ or ""
    assert "2026-09-11" in doc, f"{name} 的 docstring 未记录定论日期"
    assert ("已接线" in doc) or ("不接线" in doc), f"{name} 的 docstring 未给出明确结论"
    assert "ms" in doc, f"{name} 的 docstring 未给出实测数字（结论必须有证据）"


def test_jsonl_load_worker_is_actually_wired() -> None:
    """`JsonlLoadWorker` 已接线：任务历史必须按大小分流，而不是一律同步解析。"""
    from omnicrawler.gui.views import task_history

    assert hasattr(task_history, "_SYNC_PARSE_MAX_BYTES")
    view_source = Path(task_history.__file__ or "")
    text = view_source.read_text(encoding="utf-8")
    assert "JsonlLoadWorker" in text, "task_history 没有引用 JsonlLoadWorker，接线回退了"
    assert "_reload_pending" in text, "加载期间的重入保护缺失（新增记录可能被覆盖）"


def test_unwired_workers_do_not_claim_to_be_wired() -> None:
    """反面守卫：明确判定「不接线」的两类，docstring 不得声称已接线。"""
    for name in ("SqliteQueryWorker", "TemplateCombineWorker"):
        doc = getattr(workers, name).__doc__ or ""
        assert "已接线" not in doc, f"{name} 判定为不接线，docstring 却声称已接线"
