"""终态词表只有一个真源：`core/run_state.py`。

## 为什么

`TERMINAL_RUN_STATES` / 「算完成」/ 「有产出」这三件事在同一个运行状态上对应三个**不同**的
问题，此前各自被写成裸字面量集合散落在 GUI 与运行时里：

* `runtime/execution_backend.py` 两处判断「上一个 worker 是否已结束」；
* `gui/runner/worker_task_runner.py` 判断「是否进入终态分支」；
* `gui/delegates/run_controller.py` 两处分别判断「能否载入已采到的结果」与「是否算完成」；
* `services/benchmarking.py` 判断「这次基准能不能采信」。

裸字面量的问题不是重复本身，而是**改词表时没有任何东西会红**：往 `TERMINAL_RUN_STATES`
里加一个状态，散落的副本静默保持旧口径。本文件把三件事钉在 `core/run_state.py` 上，
并用反向断言证明「把裸字面量装回去 ⇒ 必须红」。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "omnicrawler"

#: 唯一允许定义终态词表的模块
_TRUTH_SOURCE = "core/run_state.py"

#: 「四个终态一起出现」的裸字面量形态（顺序无关：逐个词都出现才算命中）
_TERMINAL_WORDS = ("succeeded", "failed", "cancelled", "partial_success")


def _scan_bare_terminal_sets(source: str) -> list[str]:
    """返回 `source` 里出现的「裸终态集合」片段（用于正/反向断言）。"""
    offenders: list[str] = []
    for line in source.splitlines():
        if all(f'"{word}"' in line for word in _TERMINAL_WORDS):
            offenders.append(line.strip())
    return offenders


# ── 1. 真源本身：三个集合的语义必须互相自洽 ─────────────────────────────


def test_terminal_states_are_the_declared_four() -> None:
    from omnicrawler.core.run_state import TERMINAL_RUN_STATES

    assert TERMINAL_RUN_STATES == frozenset(
        {"succeeded", "failed", "cancelled", "partial_success"}
    )


def test_delivered_states_drop_only_failed() -> None:
    """「有产出」= 终态去掉 `failed`：取消也保留已采到的结果，失败没有。"""
    from omnicrawler.core.run_state import DELIVERED_RUN_STATES, TERMINAL_RUN_STATES

    assert DELIVERED_RUN_STATES == TERMINAL_RUN_STATES - {"failed"}
    assert "cancelled" in DELIVERED_RUN_STATES, "取消要能载入已采到的结果"
    assert "failed" not in DELIVERED_RUN_STATES


def test_success_states_are_a_strict_subset_of_terminal() -> None:
    """「算完成」必须是终态的真子集——否则提示音/自动导出会对着运行中触发。"""
    from omnicrawler.core.run_state import SUCCESS_RUN_STATES, TERMINAL_RUN_STATES

    assert SUCCESS_RUN_STATES == frozenset({"succeeded", "partial_success"})
    assert SUCCESS_RUN_STATES < TERMINAL_RUN_STATES


def test_every_state_family_member_is_a_known_run_state() -> None:
    """新加状态时若忘了登记进 `RUN_STATES`，`canonical_run_state` 会抛错。"""
    from omnicrawler.core.run_state import (
        DELIVERED_RUN_STATES,
        RUN_STATES,
        SUCCESS_RUN_STATES,
        TERMINAL_RUN_STATES,
    )

    for name, family in (
        ("TERMINAL_RUN_STATES", TERMINAL_RUN_STATES),
        ("DELIVERED_RUN_STATES", DELIVERED_RUN_STATES),
        ("SUCCESS_RUN_STATES", SUCCESS_RUN_STATES),
    ):
        unknown = sorted(family - RUN_STATES)
        assert not unknown, f"{name} 含未登记的运行状态：{unknown}"


# ── 2. GUI 表现层的判据必须与核心集合一致 ────────────────────────────────


def test_gui_predicates_agree_with_the_core_sets() -> None:
    from omnicrawler.core.run_state import (
        DELIVERED_RUN_STATES,
        SUCCESS_RUN_STATES,
        TERMINAL_RUN_STATES,
    )
    from omnicrawler.gui.core.run_states import is_delivered, is_success, is_terminal

    for state in TERMINAL_RUN_STATES:
        assert is_terminal(state)
    for state in DELIVERED_RUN_STATES:
        assert is_delivered(state)
    for state in SUCCESS_RUN_STATES:
        assert is_success(state)
    assert not is_delivered("failed")
    assert not is_success("failed") and not is_success("cancelled")


def test_gui_predicates_normalise_legacy_words() -> None:
    """旧词 `finished` / `error` 走别名归一后仍要判对（历史记录读进来必须可用）。"""
    from omnicrawler.gui.core.run_states import is_delivered, is_success, is_terminal

    assert is_terminal("finished") and is_success("finished") and is_delivered("finished")
    assert is_terminal("error") and not is_success("error") and not is_delivered("error")


# ── 3. 源码守卫：不许再写裸字面量 ────────────────────────────────────────


def test_scanner_flags_a_bare_literal_set() -> None:
    """反向断言的前半：扫描器必须真的能认出裸字面量（否则下面那条恒真）。"""
    sample = 'if status in {"succeeded", "failed", "cancelled", "partial_success"}:\n'
    assert _scan_bare_terminal_sets(sample), "扫描器认不出裸终态集合，守卫等于没装"


def test_no_module_but_the_truth_source_spells_out_the_terminal_set() -> None:
    """除 `core/run_state.py` 外，任何模块都不得再写「四个终态」的裸字面量。"""
    offenders: list[str] = []
    scanned = 0
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(_SRC_ROOT).as_posix()
        scanned += 1
        if relative == _TRUTH_SOURCE:
            continue
        for snippet in _scan_bare_terminal_sets(path.read_text(encoding="utf-8")):
            offenders.append(f"{relative}: {snippet}")
    assert scanned > 0, "empty selection is not a pass：一个 .py 都没扫到"
    assert not offenders, (
        "终态词表只应在 `core/run_state.py` 定义一次；下列位置又写了裸字面量：\n  "
        + "\n  ".join(offenders)
        + "\n改用 TERMINAL_RUN_STATES / DELIVERED_RUN_STATES / SUCCESS_RUN_STATES，"
        "或 GUI 侧的 is_terminal / is_delivered / is_success。"
    )


def test_reverse_assertion_bare_literal_installed_back_turns_the_guard_red() -> None:
    """反向断言：把裸字面量装回真实文件 ⇒ 守卫必须红并指名该文件。

    只读文件、在内存里还原缺口，不写盘。
    """
    target = _SRC_ROOT / "runtime" / "execution_backend.py"
    original = target.read_text(encoding="utf-8")
    assert "TERMINAL_RUN_STATES" in original, "前置条件：该文件应已改用真源常量"
    restored = original.replace(
        "not in TERMINAL_RUN_STATES",
        'not in {"succeeded", "failed", "cancelled", "partial_success"}',
    )
    assert restored != original, "前置条件：还原后文本必须与当前不同"
    offenders = _scan_bare_terminal_sets(restored)
    assert offenders, (
        "把裸字面量装回去之后扫描器仍没报——说明守卫抓不到真实缺口（不是真守卫）"
    )


def test_benchmark_ok_statuses_come_from_the_shared_success_set() -> None:
    """基准「可采信」用的是同一份成功集合，不是又一份手抄。"""
    from omnicrawler.core.run_state import SUCCESS_RUN_STATES
    from omnicrawler.services.benchmarking import _OK_STATUSES

    assert _OK_STATUSES == SUCCESS_RUN_STATES


def test_pytest_source_root_is_where_we_think_it_is() -> None:
    """锚定路径，避免 rglob 扫到空目录后所有断言恒真。"""
    assert (_SRC_ROOT / _TRUTH_SOURCE).is_file()
    assert len(list(_SRC_ROOT.rglob("*.py"))) > 100, "源码树看起来没扫到东西"
    assert not re.search(r"\s", str(_SRC_ROOT)), "路径里不该有空白，否则后续匹配易错"
