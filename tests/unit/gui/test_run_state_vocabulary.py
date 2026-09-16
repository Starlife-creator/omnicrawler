"""GUI 运行状态词表：单一来源 + 旧词兼容读取 + `partial_success` 不再被压平（W6.7 主项）。

## 背景

同一个运行状态此前在 GUI 里有**五份各写各的表**，而且混用两套词：后端返回规范词
（`succeeded` / `failed` / `cancelled` / `partial_success`），`worker_task_runner` 却把它**压成**
`finished` / `error`；`run_controller` / `status_indicator` / `task_history` / `home` 各自维护
「状态 → 文案 / 颜色 / 图标」。代价之一：**`partial_success` 被压成 `finished` 后，
"部分成功"在界面上和"完全成功"再也分不出来**（而这正是"存在错误记录"的提示依据）。

现在：唯一真源是 `core/run_state.py`，GUI 的表现层集中在 `gui/core/run_states.py`。
本文件钉住四件事：

1. **兼容读取**：`finished` / `error` 是别名，归一后是 `succeeded` / `failed`；
2. **归一永不抛错**：界面路径上遇到没见过的值必须原样返回（不让 UI 崩）；
3. **`partial_success` 保持独立**（文案与成功不同、图标不同、判终态为真）；
4. **生产者只发规范词**，且**五处消费者都从同一模块取**（源码级守卫）。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUI_ROOT = _REPO_ROOT / "src" / "omnicrawler" / "gui"

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="GUI 测试需要 PySide6"
)

#: 状态词表统一后，本该只从 `gui.core.run_states` 取表现的五处消费者
_CONSUMERS = (
    "runner/worker_task_runner.py",
    "delegates/run_controller.py",
    "widgets/status_indicator.py",
    "views/task_history.py",
    "home.py",
)


# ── 1. 兼容读取 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("legacy", "canonical"), [("finished", "succeeded"), ("error", "failed")])
def test_legacy_words_still_read(legacy: str, canonical: str) -> None:
    """`finished` / `error` 是**别名**：旧历史记录 / 旧会话文件读进来必须仍然可用。"""
    from omnicrawler.core.run_state import canonical_run_state
    from omnicrawler.gui.core.run_states import normalize_state, state_label

    assert canonical_run_state(legacy) == canonical, f"核心别名表应登记 {legacy}"
    assert normalize_state(legacy) == canonical
    assert normalize_state(legacy.upper()) == canonical, "大小写不敏感"
    # 文案与规范词一致（否则界面会因为历史记录的旧词显示成裸词）
    assert state_label(legacy) == state_label(canonical)


def test_normalize_never_raises() -> None:
    """界面路径上的归一**不许抛错**：没见过的词原样返回即可。"""
    from omnicrawler.gui.core.run_states import normalize_state, state_icon, state_label

    for value in ("", "  ", "unknown-state", "完成", "RUNNING", "weird/thing"):
        normalized = normalize_state(value)
        assert isinstance(normalized, str)
        assert state_label(value), "文案至少得是非空字符串"
        assert state_icon(value)


def test_gui_only_states_are_kept() -> None:
    """GUI 专有生命周期词（`idle` / `stopping`）不在核心状态机里，但必须被保留而不是被清成空。"""
    from omnicrawler.gui.core.run_states import GUI_ONLY_STATES, normalize_state

    for word in GUI_ONLY_STATES:
        assert normalize_state(word) == word
    assert normalize_state("") == "idle", "空值应回落到空闲"


# ── 2. `partial_success` 不再被压平 ──────────────────────────────────────


def test_partial_success_is_distinct_from_success() -> None:
    """W6.7 的实际收益：部分成功与完全成功在界面上必须能区分。"""
    from omnicrawler.gui.core.run_states import is_terminal, state_icon, state_label

    assert state_label("partial_success") != state_label("succeeded")
    assert state_icon("partial_success") != state_icon("succeeded")
    assert is_terminal("partial_success") and is_terminal("succeeded")
    assert is_terminal("finished"), "旧词也要判成终态（兼容）"


def test_every_canonical_state_has_a_label_and_icon() -> None:
    """核心词表里的每个状态都要有界面文案与图标（新加状态不许在界面上显示成裸词）。"""
    from omnicrawler.core.run_state import RUN_STATES
    from omnicrawler.gui.core.run_states import state_icon, state_label

    missing_label = [s for s in sorted(RUN_STATES) if state_label(s) == s]
    missing_icon = [s for s in sorted(RUN_STATES) if state_icon(s) == "•"]
    assert not missing_label, f"这些状态没有文案：{missing_label}"
    assert not missing_icon, f"这些状态没有图标：{missing_icon}"


# ── 3. 单一来源（源码守卫） ──────────────────────────────────────────────


def test_producers_emit_canonical_words_only() -> None:
    """生产者不得再把规范词压成 `finished` / `error`。"""
    source = (_GUI_ROOT / "runner" / "worker_task_runner.py").read_text(encoding="utf-8")
    offenders = re.findall(r'_set_state\(\s*[\'"](finished|error)[\'"]', source)
    assert not offenders, (
        f"`worker_task_runner._set_state` 仍在发旧词 {offenders} —— "
        f"应当发核心规范词（partial_success 被压成 finished 会让界面分不出部分成功）"
    )


@pytest.mark.parametrize("relative", _CONSUMERS)
def test_consumers_take_presentation_from_the_shared_module(relative: str) -> None:
    """五处消费者都必须从 `gui.core.run_states` 取文案/颜色/图标。"""
    source = (_GUI_ROOT / relative).read_text(encoding="utf-8")
    assert "core.run_states import" in source, (
        f"{relative} 没有从 `gui.core.run_states` 取状态表现 —— "
        f"五份各写各的表正是 W6.7 要消掉的东西"
    )


@pytest.mark.parametrize("relative", _CONSUMERS)
def test_consumers_do_not_redefine_state_maps(relative: str) -> None:
    """守卫：不得再出现「以旧词为键的状态映射」（形如 `"finished": ...`）。"""
    source = (_GUI_ROOT / relative).read_text(encoding="utf-8")
    offenders = re.findall(r'[\'"](finished|error)[\'"]\s*:', source)
    assert not offenders, (
        f"{relative} 里又出现了以旧词为键的映射 {offenders} —— 状态映射只应在 "
        f"`gui/core/run_states.py` 定义一次"
    )
