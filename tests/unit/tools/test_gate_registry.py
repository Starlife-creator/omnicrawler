"""门禁清单与 CI 必须双向一致——漂移要变成测试失败，而不是悄悄发生的分歧。

起因（2026-09-11 实测）：仓库的 `tools/check_*.py` 门禁 CI 全都跑了，
但 `CONTRIBUTING.md` 的门禁表只列了 5 项，本地也没有聚合入口。
于是「照文档自证」会漏掉多项，而且没有任何机制能发现这件事。

本文件把「清单 ⇄ CI」的一致性变成断言。**注意两侧都断言**：

* 漏登记（CI 跑了但清单里没有）→ 失败；
* 漏接线（清单里有但 CI 不跑）→ 失败；
* 磁盘上有、但既没登记也没接线的 `check_*.py` → 失败（除非在库白名单里显式说明）。

另外先断言「确实扫到了脚本」，否则正则写错会让双向比较变成空集对空集的**假通过**。

**边界（如实说明）**：这里的比对分两层。①**脚本集合**必须双向一致；②**旗标集合**：
workflow 里含 `${{ matrix.profile }}` 之类的插值、`"$RUNNER_TEMP/..."` 路径与反引号续行，
逐字比较会脆，所以只比旗标名（`--profile` / `--sbom` …），**不比旗标的值**。
CI 用了而清单未声明的旗标判红；声明了却不在 CI 出现的旗标同样判红。
仍然覆盖不到的部分：同一旗标的值在两侧是否等价（例如 `--profile core` vs `--profile browser`），
这需要人读 diff 判断，靠 code review 兜。
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
    SKIP_EXIT_CODE,
    Gate,
    ci_managed_scripts,
    gates_for,
)
from tools.self_verify import main

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW_DIR = _REPO_ROOT / ".github" / "workflows"

# 门禁脚本的命名约定是 `check_*.py`，但**不是所有门禁都叫这个名字**
# （例：`tools/post_bump_steps.py --check-current` 是版本收口门禁）。
# 这里显式列出这类脚本——不改成「扫 tools/ 下所有 .py」是**有意**的：
# CI 里还有构建与辅助脚本（build_wheel.py、i18n_stats.py、generate_sbom.py、
# checkout_market.py …），它们不是门禁，一律纳入会让双向断言立刻变成噪声红。
_NON_CHECK_GATE_SCRIPTS = ("post_bump_steps.py",)
_SCRIPT_RE = re.compile(
    r"tools/(check_[a-z0-9_]+\.py|"
    + "|".join(re.escape(name) for name in _NON_CHECK_GATE_SCRIPTS)
    + r")"
)


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


#: 白名单条目必须由**仓内真实消费者**背书：构建脚本、其它门禁、或测试真正 import 它。
#: 只写进白名单、没有任何调用方的脚本，就是「绕过校验的缺口」本身——它一旦出现在
#: `tools/check_*.py` 下就被白名单静默豁免，永远没人发现它既没登记也没被调用。
_WHITELIST_CONSUMER_ROOTS = ("tests", "tools")
_WHITELIST_CONSUMER_FILES = ("build_linux.sh", "build_windows.ps1", "build_macos.sh")


def _whitelist_consumers(script_stem: str) -> list[str]:
    """返回真正引用该脚本的仓内文件（相对路径）。

    刻意**跳过测试文件自身**：白名单定义在 test_gate_registry.py 里，若允许它自证，
    任何写进白名单的名字都会「被引用」——那正是要防的自证循环。
    只扫 ``tests/`` 与 ``tools/`` 两个目录，外加根目录的构建脚本：这三处是白名单脚本
    唯一可能的调用方，全仓 ``rglob`` 既慢又会扫进 ``.audit-tmp`` 之类的历史快照。
    """
    this_file = Path(__file__).resolve()
    candidates: list[Path] = []
    for root_name in _WHITELIST_CONSUMER_ROOTS:
        root = _REPO_ROOT / root_name
        if root.is_dir():
            candidates.extend(root.rglob("*.py"))
            candidates.extend(root.rglob("*.sh"))
    for name in _WHITELIST_CONSUMER_FILES:
        candidate = _REPO_ROOT / name
        if candidate.is_file():
            candidates.append(candidate)
    hits: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", ".tmp", ".audit-tmp", ".agents", "node_modules"}
               for part in path.relative_to(_REPO_ROOT).parts):
            continue
        if path.resolve() == this_file:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if script_stem in text:
            hits.append(str(path.relative_to(_REPO_ROOT)))
    return hits


def test_library_only_scripts_have_real_consumers() -> None:
    """白名单不是「塞进去就免疫」：每一条都必须有仓内真实调用方。

    这条补的是白名单自身的漏洞——`test_no_gate_script_is_silently_unaccounted` 给白名单
    开了豁免口子，若白名单可以随便加名字，那个口子就是「加个名字绕过校验」的通行证。
    """
    orphans = [
        script
        for script in sorted(_LIBRARY_ONLY_SCRIPTS)
        if not _whitelist_consumers(Path(script).stem)
    ]
    assert not orphans, (
        f"以下库白名单条目没有任何仓内消费者（既非构建脚本调用、也非测试/其它门禁 import）: "
        f"{orphans}；没有调用方就不该被豁免——要么接上调用方，要么登记为门禁"
    )


def test_whitelist_consumer_scan_is_not_vacuous() -> None:
    """反向断言：扫描必须真能发现消费者，也要真能发现「没有消费者」。

    两个方向都断言，否则正则/路径写错时上一条会变成「永远为真」的空断言。
    """
    assert _whitelist_consumers("check_linux_delivery"), (
        "连真实存在的消费者都扫不到 ⇒ 上一条的判据是假的"
    )
    assert not _whitelist_consumers("check_this_script_does_not_exist")


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
        # 声明了 CI 旗标就同样要解释差异——声明不是免审。
        if gate.declared_ci_flags:
            assert gate.ci_note, f"{gate.name} 声明了 CI 旗标却没说与本地自证的差异"


# ---------- 旗标级比对：脚本名一致但参数不同，同样是「清单没说实话」 ----------
#
# 边界（原文档已如实写明）：按脚本名做集合比对覆盖不到**参数**。这里把它补上——
# 不是逐字比较整条命令（workflow 里有 `${{ matrix.profile }}`、`"$RUNNER_TEMP/..."`、
# 反引号续行，逐字比必脆），而是只比**旗标集合**：CI 用了而清单没声明的旗标 ⇒ 判红。

_CI_COMMAND_RE = re.compile(
    r"(?:python|py)\s+(tools/(?:check_[a-z0-9_]+|post_bump_steps)\.py)\b([^\n]*)"
)


def _merge_continuations(text: str) -> list[str]:
    """把 `\\` 与 PowerShell 反引号续行合并成逻辑行，否则旗标会落在下一行扫不到。"""
    merged: list[str] = []
    buffer = ""
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\") or stripped.endswith("`"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        merged.append(buffer + stripped)
        buffer = ""
    if buffer:
        merged.append(buffer)
    return merged


def _flags_in(rest: str) -> set[str]:
    """取出命令尾部里的旗标名（`--dir=X` 归一成 `--dir`，值不是旗标）。"""
    return {
        token.split("=", 1)[0]
        for token in rest.split()
        if token.startswith("-") and not token.startswith("---")
    }


def _ci_flags_by_script() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for workflow in sorted(_WORKFLOW_DIR.glob("*.yml")):
        for line in _merge_continuations(workflow.read_text(encoding="utf-8")):
            for match in _CI_COMMAND_RE.finditer(line):
                script, rest = f"tools/{match.group(1).removeprefix('tools/')}", match.group(2)
                found.setdefault(script, set()).update(_flags_in(rest))
    return found


def test_ci_flag_scan_is_not_vacuous() -> None:
    """先证明扫描确实抓到了旗标——否则下面的比对会变成「空集 ⊆ 任何集合」的假通过。"""
    by_script = _ci_flags_by_script()
    assert by_script, "一条 CI 门禁命令都没扫到，正则或目录已失效"
    assert by_script.get("tools/check_coverage_gates.py", set()) >= {"--profile"}
    assert by_script.get("tools/check_release_integrity.py", set()) >= {"--wheel-dir"}


def test_ci_flags_are_declared_in_the_registry() -> None:
    """CI 实际使用的旗标必须被清单**声明**：`args` 自带、`needs_args` 映射、或 `declared_ci_flags`。

    补的正是原文档承认的边界——脚本名对得上、参数悄悄多了/少了一个，此前无人发现。
    反向断言见下一条：声明了却没在 CI 出现，同样判红，防止把旗标写成静态白名单。
    """
    by_script = _ci_flags_by_script()
    offenders: list[str] = []
    for gate in GATES:
        if gate.script is None:
            continue
        allowed = {arg for arg in gate.args if arg.startswith("-")}
        allowed |= {f"--{name}" for name in gate.needs_args}
        allowed |= set(gate.declared_ci_flags)
        undeclared = by_script.get(gate.script, set()) - allowed
        if undeclared:
            offenders.append(f"{gate.name}: {sorted(undeclared)}")
    assert not offenders, (
        f"以下门禁在 CI 里用了清单未声明的旗标（本地自证与 CI 已不一致而无人察觉）: {offenders}；"
        "请在 Gate 上补 declared_ci_flags 并写清差异"
    )


def test_declared_ci_flags_actually_appear_in_ci() -> None:
    """**反向断言**：声明了就必须真的在 CI 出现，否则声明会退化成「随便写都算数」的白名单。"""
    by_script = _ci_flags_by_script()
    stale: list[str] = []
    for gate in GATES:
        if gate.script is None or not gate.declared_ci_flags:
            continue
        present = by_script.get(gate.script, set())
        missing = set(gate.declared_ci_flags) - present
        if missing:
            stale.append(f"{gate.name}: {sorted(missing)}")
    assert not stale, f"以下声明的 CI 旗标在 workflow 里找不到（接线已变或已删）: {stale}"


def test_static_set_covers_the_red_lines() -> None:
    """`static` 是「干净检出即可自证」的集合，必须覆盖各项红线。"""
    names = {gate.name for gate in gates_for(("static",))}
    required = {
        "compileall", "ruff", "mypy", "templates_validate",
        "check_architecture", "check_docs_consistency", "check_gui_conventions",
        "check_lint_budget", "check_network_boundaries", "check_release_integrity",
        "check_sdk_api", "check_cli_docs", "check_coding_standards", "check_minimal_install",
        # 版本收口：锚点页声明的「当前版本」与 pyproject 一致（曾两次静默漂移）
        "post_bump_steps",
        # 锚段完整性：唯一「不可擅动」的真源恰好零守卫（根目录不是 git 仓）
        "check_anchor_integrity",
        # 静默吞异常：宽类型 except + 单条 pass/continue 必须留痕
        "check_silent_exception_handlers",
        # 公开的 .gitignore：注释不得泄露本机布局，且它自己不得被忽略
        "check_gitignore_hygiene",
    }
    assert required <= names, f"static 集合缺少: {sorted(required - names)}"


def test_all_set_includes_every_gate() -> None:
    assert set(gates_for(GATE_SETS)) == set(GATES)


def test_docs_do_not_hardcode_gate_counts() -> None:
    """文档不得写死门禁数量——写死的数字必然漂移。

    由来（批次 2 判据可信）：`tools/gate_registry.py` 的 docstring 曾写「现有 19 个
    `tools/check_*.py`」，而磁盘上早已是 28 个；`CONTRIBUTING.md` 同样写死「13 项」。
    两处都靠人手同步，于是「文档说 19、实际 28」长期共存且无人发现。

    **2026-11-14 修正**：这条判据原先只认「N 项 `tools/check_`」一种句式、只扫
    `CONTRIBUTING.md` 与 `tools/gate_registry.py` 两个文件，于是同类的其他形态
    （手抄的测试通过数、i18n 条目数、别的目录下的文档）全都漏在扫描面之外。
    判据本体已移交 `tools/check_doc_numbers.py`（扫描面 + 句式 + 生成块逐字比对，
    见 `tests/unit/tools/test_doc_numbers_gate.py` 的五条反向断言）。

    本用例保留为**扫描面不得收窄**的守卫：新判据必须覆盖旧判据守过的每一处，
    否则这次「扩宽」会在下一次重构里被悄悄退回去。
    """
    from tools import check_doc_numbers as doc_gate

    scanned, _ = doc_gate.scan_targets(_REPO_ROOT)
    covered = {path.relative_to(_REPO_ROOT).as_posix() for path in scanned}
    # 旧判据守过的两个对象，一个都不能从扫描面里掉出去。
    assert {"CONTRIBUTING.md", "tools/gate_registry.py"} <= covered, (
        f"扫描面收窄了，缺少旧判据守过的文件: {sorted({'CONTRIBUTING.md', 'tools/gate_registry.py'} - covered)}"
    )
    # 旧的句式必须仍被新判据抓住——否则「移交」等于取消。
    findings = doc_gate.check_text("CONTRIBUTING.md", "以及 10 项 `tools/check_*.py`（架构、SDK 契约）")
    assert "gate-count-phrase" in {finding.rule for finding in findings}, findings


def test_unknown_set_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知门禁集合"):
        gates_for(("nonsense",))


def test_skipped_gates_are_reported_and_not_treated_as_passed(tmp_path: Path) -> None:
    """跳过必须**显式列出并给出原因**，且**不能算作通过**。

    批次 2 修正（优化方案 §14.3）：此前 `ok` 初始化为 True、只在失败时置 False，
    于是「全 skipped」也判 ok=True、退出 0——空集合被当成通过混进证据链。
    现在结论是三态，全跳过 ⇒ `unverified` ⇒ 非 0 退出。
    """
    report_path = tmp_path / "evidence.json"
    code = main(["--set", "install", "--json", str(report_path)])

    assert code == 1, "全部跳过时不得返回 0（跳过不等于通过）"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["skipped"] >= 1
    assert payload["passed"] == 0
    assert payload["failed"] == 0
    skipped = [gate for gate in payload["gates"] if gate["status"] == "skipped"]
    assert all(gate["detail"] for gate in skipped), "跳过必须写明原因"
    assert payload["verdict"] == "unverified"
    assert payload["ok"] is False, "全跳过不得记为 ok"


def test_reverse_assertion_empty_selection_is_not_a_pass(tmp_path: Path) -> None:
    """反向断言：把「全跳过仍判通过」的缺口装回去，本用例必须红。

    直接构造「只有 skipped、没有 passed」的报告，断言 `verdict` 不是 `passed`——
    若有人把 `ok` 改回「只在失败时置 False」，这里立刻失败。
    """
    from tools.self_verify import EvidenceReport, GateOutcome

    report = EvidenceReport(
        ok=True, sets=["install"], started_at="now", duration_seconds=0.0,
        head="deadbee", dirty=False,
        gates=[GateOutcome("check_extra_install", "skipped", 0.0, "需要参数 profile")],
    )
    assert report.passed == 0 and report.failed == 0
    assert report.verdict == "unverified"
    assert not report.ok or report.verdict != "passed"


def test_mixed_pass_and_skip_is_a_pass(tmp_path: Path) -> None:
    """只要有门禁真正通过、且没有失败，结论仍是通过（不能矫枉过正）。"""
    from tools.self_verify import EvidenceReport, GateOutcome

    report = EvidenceReport(
        ok=False, sets=["static"], started_at="now", duration_seconds=0.0,
        head="deadbee", dirty=False,
        gates=[
            GateOutcome("ruff", "passed", 0.1),
            GateOutcome("check_extra_install", "skipped", 0.0, "需要参数 profile"),
        ],
    )
    assert report.verdict == "passed"


def test_missing_prerequisite_is_reported_with_reason(tmp_path: Path) -> None:
    """缺前置文件的覆盖率门禁应被跳过并说明缺的是哪个文件。"""
    report_path = tmp_path / "evidence.json"
    main(["--set", "coverage", "--json", str(report_path)])
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    coverage = next(gate for gate in payload["gates"] if gate["name"] == "check_coverage_gates")
    assert coverage["status"] == "skipped"
    assert "coverage.json" in coverage["detail"]


# ---------- 门禁自述跳过（SKIP_EXIT_CODE）的三条边界 ----------


def _synthetic_gate(code_expr: str) -> Gate:
    """构造一条只做一件事的门禁：按给定表达式退出。"""
    return Gate(
        name="synthetic",
        args=("-c", f"import sys; {code_expr}"),
        sets=frozenset({"static"}),
        description="仅用于测试跳过码语义",
    )


def test_gate_exit_77_is_recorded_as_skipped_with_reason() -> None:
    """门禁以跳过码退出 ⇒ `skipped`，理由取输出末行（约定：门禁把缺的能力写在末行）。"""
    from tools.self_verify import _run_gate

    gate = _synthetic_gate('print("bash 语法检查无法执行：缺命名管道"); sys.exit(77)')
    outcome = _run_gate(gate)
    assert outcome.status == "skipped", outcome
    assert outcome.detail.startswith("bash 语法检查无法执行"), outcome.detail


def test_gate_exit_77_without_reason_is_still_accounted() -> None:
    """跳过码但没写原因 ⇒ 仍记 `skipped`，并把「没写原因」显式记账（跳过不许无声）。"""
    from tools.self_verify import _run_gate

    outcome = _run_gate(_synthetic_gate("sys.exit(77)"))
    assert outcome.status == "skipped"
    assert "未写明原因" in outcome.detail, outcome.detail


def test_any_other_failure_code_is_still_a_failure() -> None:
    """**反向断言**：除跳过码以外的一切非 0 都必须判 `failed`——否则跳过码就成了逃生门。"""
    from tools.self_verify import _run_gate

    for code in (1, 2, 76, 78, 255):
        outcome = _run_gate(_synthetic_gate(f'sys.exit({code})'))
        assert outcome.status == "failed", f"退出码 {code} 被误判成 {outcome.status}"


def test_skip_code_is_not_zero() -> None:
    """跳过码必须非 0：与「通过」共用退出码会让 subprocess 层直接丢掉跳过这件事。"""
    assert SKIP_EXIT_CODE != 0


def test_skip_does_not_make_the_run_green(tmp_path: Path) -> None:
    """**反向断言**：一条门禁被跳过不能让整轮自证从 failed 变 passed。

    构造「failed + skipped」组合：结论仍必须是 failed，且 `ok` 为假。
    """
    from tools.self_verify import EvidenceReport, GateOutcome

    report = EvidenceReport(
        ok=True, sets=["static"], started_at="now", duration_seconds=0.0,
        head="deadbee", dirty=False,
        gates=[
            GateOutcome("ruff", "passed", 0.1),
            GateOutcome("check_shell_scripts", "skipped", 0.2, "bash 不可用"),
            GateOutcome("mypy", "failed", 0.3, "退出码 1"),
        ],
    )
    assert report.verdict == "failed", "有失败项时不能因存在跳过项而变通过"


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
