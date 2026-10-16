"""门禁清单与 CI 必须双向一致——漂移要变成测试失败，而不是悄悄发生的分歧。

起因（2026-09-11 实测）：仓库的 `tools/check_*.py` 门禁 CI 全都跑了，
但 `CONTRIBUTING.md` 的门禁表只列了 5 项，本地也没有聚合入口。
于是「照文档自证」会漏掉多项，而且没有任何机制能发现这件事。

本文件把「清单 ⇄ CI」的一致性变成断言。**注意两侧都断言**：

* 漏登记（CI 跑了但清单里没有）→ 失败；
* 漏接线（清单里有但 CI 不跑）→ 失败；
* 磁盘上有、但既没登记也没接线的 `check_*.py` → 失败（除非在库白名单里显式说明）。

另外先断言「确实扫到了脚本」，否则正则写错会让双向比较变成空集对空集的**假通过**。

**边界（如实说明）**：这里比较的是**脚本集合**，不是逐字命令行——workflow 里含
`${{ matrix.profile }}` 之类的插值，逐字比较会脆。因此门禁的*参数*（如 `--sbom`）
仍可能与本地不一致；这是本测试覆盖不到的部分，靠 code review 兜。
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

from tools.gate_registry import (
    GATE_SETS,
    GATES,
    ci_managed_scripts,
    gates_for,
)
from tools.self_verify import main

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW_DIR = _REPO_ROOT / ".github" / "workflows"
_SCRIPT_RE = re.compile(r"tools/(check_[a-z0-9_]+\.py)")


def _scripts_referenced_by_ci() -> set[str]:
    found: set[str] = set()
    for workflow in sorted(_WORKFLOW_DIR.glob("*.yml")):
        for match in _SCRIPT_RE.findall(workflow.read_text(encoding="utf-8")):
            found.add(f"tools/{match}")
    return found


def test_ci_reference_scan_is_not_vacuous() -> None:
    """先确认扫描确实有收获——否则下面的双向比较会变成空集对空集的假通过。"""
    referenced = _scripts_referenced_by_ci()
    assert len(referenced) >= 10, f"只扫到 {len(referenced)} 个脚本，正则或目录可能已失效: {referenced}"


def test_every_ci_gate_is_registered() -> None:
    """CI 跑了的门禁必须出现在清单里（漏登记 → 本地自证会漏跑）。"""
    missing = _scripts_referenced_by_ci() - set(ci_managed_scripts())
    assert not missing, f"以下门禁在 CI 中执行但未登记到 gate_registry: {sorted(missing)}"


def test_every_registered_gate_is_wired_into_ci() -> None:
    """清单里的门禁必须真的被 CI 执行（漏接线 → 清单承诺了不存在的保障）。"""
    unwired = set(ci_managed_scripts()) - _scripts_referenced_by_ci()
    assert not unwired, f"以下门禁已登记但没有任何 workflow 执行: {sorted(unwired)}"


# 非门禁的 `tools/check_*.py`：被构建脚本或测试 import 的**校验库**，没有独立 CLI 入口。
# 显式登记在案，既避免被误判为「漏登记的门禁」，也保证新增脚本时不会无人察觉。
_LIBRARY_ONLY_SCRIPTS = frozenset({
    "tools/check_linux_delivery.py",   # build_linux.sh 与单测共用的判据函数
    "tools/check_scenario_cards.py",   # 被 tests 直接 import 的校验库
})


def test_no_gate_script_is_silently_unaccounted() -> None:
    """磁盘上的每个 `tools/check_*.py` 要么登记为门禁，要么在库白名单里显式说明。

    原有的双向断言只比较「CI 引用」与「清单登记」，两者都是脚本的子集——
    磁盘上多出的脚本（既没登记也没接线）不会被发现。这一条补上第三向。
    """
    on_disk = {f"tools/{path.name}" for path in (_REPO_ROOT / "tools").glob("check_*.py")}
    unaccounted = on_disk - set(ci_managed_scripts()) - _LIBRARY_ONLY_SCRIPTS
    assert not unaccounted, (
        f"以下 check_*.py 既未登记为门禁、也不在库白名单: {sorted(unaccounted)}"
    )


def test_library_only_scripts_are_not_registered() -> None:
    """库白名单里的脚本不应同时被登记为门禁——否则白名单会掩盖真正的接线缺失。"""
    overlap = _LIBRARY_ONLY_SCRIPTS & set(ci_managed_scripts())
    assert not overlap, f"库白名单与已登记门禁重叠: {sorted(overlap)}"


def test_gate_names_and_sets_are_well_formed() -> None:
    names = [gate.name for gate in GATES]
    assert len(names) == len(set(names)), f"门禁名重复: {names}"
    for gate in GATES:
        assert gate.sets, f"{gate.name} 未归属任何集合"
        assert gate.sets <= set(GATE_SETS), f"{gate.name} 含未知集合: {gate.sets - set(GATE_SETS)}"
        assert gate.args, f"{gate.name} 没有可执行参数"
        assert gate.description, f"{gate.name} 缺少说明"
        if gate.script is not None:
            assert (_REPO_ROOT / gate.script).is_file(), f"{gate.name} 指向不存在的脚本 {gate.script}"
        # 需要外部参数的门禁必须写明与 CI 的差异，否则跳过原因会不可解释。
        if gate.needs_args:
            assert gate.ci_note, f"{gate.name} 需要参数却没有说明与 CI 的差异"


def test_static_set_covers_the_red_lines() -> None:
    """`static` 是「干净检出即可自证」的集合，必须覆盖各项红线。"""
    names = {gate.name for gate in gates_for(("static",))}
    required = {
        "compileall", "ruff", "mypy", "templates_validate",
        "check_architecture", "check_docs_consistency", "check_gui_conventions",
        "check_lint_budget", "check_network_boundaries", "check_release_integrity",
        "check_sdk_api", "check_cli_docs", "check_coding_standards", "check_minimal_install",
    }
    assert required <= names, f"static 集合缺少: {sorted(required - names)}"


def test_all_set_includes_every_gate() -> None:
    assert set(gates_for(GATE_SETS)) == set(GATES)


def test_unknown_set_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知门禁集合"):
        gates_for(("nonsense",))


def test_skipped_gates_are_reported_and_not_treated_as_failure(tmp_path: Path) -> None:
    """跳过必须**显式列出并给出原因**，且不能算作通过——也不能让整体判失败。"""
    report_path = tmp_path / "evidence.json"
    code = main(["--set", "install", "--json", str(report_path)])

    assert code == 0, "仅因前置条件不足而跳过，不应判为失败"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["skipped"] >= 1
    assert payload["failed"] == 0
    skipped = [gate for gate in payload["gates"] if gate["status"] == "skipped"]
    assert all(gate["detail"] for gate in skipped), "跳过必须写明原因"
    assert payload["ok"] is True


def test_missing_prerequisite_is_reported_with_reason(tmp_path: Path) -> None:
    """缺前置文件的覆盖率门禁应被跳过并说明缺的是哪个文件。"""
    report_path = tmp_path / "evidence.json"
    main(["--set", "coverage", "--json", str(report_path)])
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    coverage = next(gate for gate in payload["gates"] if gate["name"] == "check_coverage_gates")
    assert coverage["status"] == "skipped"
    assert "coverage.json" in coverage["detail"]


def test_list_mode_lists_selected_gates(capsys) -> None:
    assert main(["--set", "static", "--list"]) == 0
    out = capsys.readouterr().out
    for name in ("ruff", "mypy", "check_architecture", "check_gui_conventions"):
        assert name in out
    assert "check_extra_install" not in out, "install 集合的门禁不应出现在 static 列表里"


def test_list_survives_a_non_utf8_console() -> None:
    """`--list` 曾在非 UTF-8 控制台因门禁描述里的 `↔`(U+2194) 抛 UnicodeEncodeError。

    Windows 默认控制台是 GBK、CI 的 cp1252 runner 同样不可编码该字符；本仓同族工具
    （check_coverage_gates / offline_acceptance / portable_archive_smoke）都有此回归，
    唯独自证入口此前漏了。用 cp1252 控制台复现（`↔` 不可编码）。
    """
    buffer = io.BytesIO()
    console = io.TextIOWrapper(buffer, encoding="cp1252")
    original = sys.stdout
    sys.stdout = console
    try:
        code = main(["--set", "static", "--list"])
    finally:
        sys.stdout = original
        console.flush()
    assert code == 0, "--list 在非 UTF-8 控制台上不应失败"
    assert buffer.getvalue(), "--list 在非 UTF-8 控制台上未输出任何内容"
