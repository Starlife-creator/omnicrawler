"""宿主生命周期前缀门禁的两条判据：逐项有操作 + **空集合判红**。

## 为什么这条门禁存在

2026-11-27：`_HOST_LIFECYCLE_OPERATION_PREFIXES` 里有 `capability.`，
而全仓**没有任何 `capability.*` 操作**。空预留会让读代码的人以为
「运行期能力协商已经存在」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO / "tools" / "gates") not in sys.path:
    sys.path.insert(0, str(REPO / "tools" / "gates"))

import check_lifecycle_prefixes as gate  # noqa: E402


def test_real_prefixes_all_have_operations() -> None:
    """真实仓库上每个前缀都必须有对应操作（否则门禁一上线就是红的）。"""
    prefixes = gate.lifecycle_prefixes(REPO)
    operations = gate.known_operations(REPO)
    assert prefixes, "前缀表不该为空"
    assert gate.uncovered_prefixes(prefixes, operations) == []


def test_operations_universe_includes_adapter_calls() -> None:
    """★ 已知操作全集必须含**适配器发起**的操作。

    `view.describe` / `resource.inventory` 不进 `CAPABILITY_VERSIONS`，
    只由适配器 `self._host.call(...)` 发起。若全集只取能力版本表，
    这两个前缀会被误判成「空预留」。
    """
    operations = gate.known_operations(REPO)
    assert "view.describe" in operations
    assert "resource.inventory" in operations


def test_capability_prefix_is_gone() -> None:
    """★ 回归：`capability.` 前缀已被删除，不许再加回来。

    它对应的操作一个都不存在（能力协商在启动插件代码前就完成了）。
    """
    assert "capability." not in gate.lifecycle_prefixes(REPO)


def test_uncovered_prefix_is_reported() -> None:
    """反向：塞一个没有操作的前缀，必须被报出来。"""
    uncovered = gate.uncovered_prefixes(("view.", "nonexistent."), frozenset({"view.describe"}))
    assert uncovered == ["nonexistent."], uncovered


def test_empty_operation_set_makes_every_prefix_uncovered() -> None:
    """空操作集时每个前缀都算未覆盖 —— 证明判据不是恒真。"""
    assert gate.uncovered_prefixes(("view.", "resource."), frozenset()) == ["view.", "resource."]


def test_main_returns_zero_on_the_real_repository() -> None:
    assert gate.main([]) == 0


def test_main_rejects_empty_prefix_table(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """★ 空集合判红。"""
    monkeypatch.setattr(gate, "lifecycle_prefixes", lambda _root: ())
    assert gate.main([]) == 1
    assert "为空" in capsys.readouterr().out


def test_main_rejects_empty_operation_universe(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """★ 操作全集为空同样判红 —— 无法证明任何前缀有对应操作。"""
    monkeypatch.setattr(gate, "known_operations", lambda _root: frozenset())
    assert gate.main([]) == 1
    assert "为空" in capsys.readouterr().out
