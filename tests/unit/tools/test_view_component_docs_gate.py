"""`view` 组件清单门禁的两条判据：逐项覆盖 + **空集合判红**。

## 为什么这条门禁存在

2026-11-27：`docs/PLUGIN_CONTRACT.md` 写市场 `view` 只允许 **7 种**组件，
而代码 `COMPONENT_TYPES` 是 **9 种** —— `text`（单行输入）与 `progress`（运行进度）
是 2026-09-24 新增的，宿主渲染已完整实现，文档一直没跟上。

这类漂移不报错也不红测试：插件作者按文档写 `view` 会发现组件用不了。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO / "tools" / "gates") not in sys.path:
    sys.path.insert(0, str(REPO / "tools" / "gates"))

import check_view_component_docs as gate  # noqa: E402


def test_component_types_match_the_declared_module() -> None:
    """门禁读的清单必须**就是**代码里的那一份（不是复制品）。"""
    from omnicrawler.plugins.plugin_declarative import COMPONENT_TYPES

    assert gate.component_types() == frozenset(COMPONENT_TYPES)


def test_real_document_covers_every_component() -> None:
    """真实文档必须覆盖全部组件 —— 这条是门禁的核心判据。"""
    document = (REPO / "docs" / "PLUGIN_CONTRACT.md").read_text(encoding="utf-8")
    assert gate.missing_components(document, gate.component_types()) == []


def test_missing_component_is_reported() -> None:
    """★ 反向：文档里少一个组件名，必须被报出来。"""
    document = "市场 view 允许 label、button、slider。"
    missing = gate.missing_components(document, frozenset({"label", "button", "progress"}))
    assert missing == ["progress"], missing


def test_word_boundary_avoids_false_positive() -> None:
    """★ 名字必须**整词**出现才算覆盖。

    若用子串判定，`progress` 会被 `progressbar` 这种无关词误判成"已覆盖"。
    """
    missing = gate.missing_components("这里是 progressbar 的说明", frozenset({"progress"}))
    assert missing == ["progress"], missing


def test_empty_component_set_is_rejected(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """★ 空集合判红 —— 它会让所有"每个都在文档里"的断言恒真。

    本项目已多次踩过「0 个用例也算通过」。
    """
    monkeypatch.setattr(gate, "component_types", lambda: frozenset())
    exit_code = gate.main([])
    assert exit_code == 1
    assert "为空" in capsys.readouterr().out


def test_main_returns_zero_on_the_real_repository() -> None:
    """真实仓库上必须绿（否则门禁一上线就是红的）。"""
    assert gate.main([]) == 0
