"""现状文档的数字必须由工具算出——判据要能被反向断言证明。

背景（2026-11-14）：`CONTRIBUTING.md` 手抄「`pytest tests/gui` = 158 passed, 1 skipped」、
`constraints/README.md` 手抄「local plugin regression (337 passed, 8 skipped)」，两处都已与实测
不符；`README.md` 一边手抄 i18n 四个数字、一边声明「README 不再手抄数字」。旧判据
（只认「N 项 `tools/check_`」一种句式、只扫两个文件）覆盖不到这些形态。

本文件断言五件事，每件都要能**变红**：

1. 生成块里改一个字符 ⇒ 红（文档侧篡改）；
2. 改真源（provider 算出的值多 1）⇒ 红（证明比的是**计算值**，不是仓库里的副本）；
3. 删掉 BEGIN/END 标记 ⇒ 红（去掉标记不等于去掉约束）；
4. 散文里夹带裸数字 ⇒ 红（句式扫描真的在扫，不是空转）；
5. 防空转：扫描集合非空、正则能命中人工构造的样例。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools import check_doc_numbers as gate

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _write(tmp_path: Path, relative: str, text: str) -> Path:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _rules(findings: list[gate.Finding]) -> set[str]:
    return {finding.rule for finding in findings}


# ---------------------------------------------------------------------------
# 防空转：扫描集合与正则都必须真的有收获
# ---------------------------------------------------------------------------


def test_scan_set_is_not_vacuous() -> None:
    """扫描面不能是空集合——否则「0 违规」与「0 个被扫」长得一模一样。"""
    scanned, excluded = gate.scan_targets(_REPO_ROOT)
    assert len(scanned) >= 20, f"只扫到 {len(scanned)} 份现状文档，glob 或跳过规则可能已失效"
    assert excluded, "一份历史文档都没被排除，历史/现状的划分可能已失效"
    names = {path.relative_to(_REPO_ROOT).as_posix() for path in scanned}
    assert "CONTRIBUTING.md" in names, "CONTRIBUTING.md 必须落在扫描面内"
    assert "README.md" in names, "README.md 必须落在扫描面内"
    assert "CHANGELOG.md" not in names, "CHANGELOG.md 是历史事实陈述，不该被现状判据扫描"


def test_history_classification_matches_its_rules() -> None:
    """历史文档的划分是判据的一部分，不能靠隐式约定。"""
    assert gate.is_history_doc("CHANGELOG.md")
    assert gate.is_history_doc("docs/TEST_REPORT.md")
    assert gate.is_history_doc("docs/TASK_ACCEPTANCE_LEDGER.md")
    assert gate.is_history_doc("docs/archive/2026-10-04-before-comprehensive/AGENTS.md")
    assert gate.is_history_doc("docs/releases/RELEASE_REPORT_1.0.1.md")
    assert gate.is_history_doc("docs/2026-10-03-something.md")
    assert not gate.is_history_doc("README.md")
    assert not gate.is_history_doc("CONTRIBUTING.md")
    assert not gate.is_history_doc("constraints/README.md")
    assert not gate.is_history_doc("docs/CODING_STANDARDS.md")


def test_forbidden_patterns_hit_constructed_samples() -> None:
    """每条禁止句式都要能命中人工样例，否则正则写错了也永远绿。"""
    samples = [
        ("gate-count-phrase", "以及 10 项 `tools/check_*.py`（架构、SDK 契约）"),
        ("gate-count-cn", "静态集合一共 29 项门禁"),
        ("gate-count-cn-rev", "门禁 29 条都在这里"),
        ("test-pass-count", "`pytest tests/gui` = 158 passed, 1 skipped"),
        ("test-pass-count-cn", "静态自证 28 通过 / 1 失败"),
        ("case-count", "证据：7 条用例覆盖了短值保留"),
        ("i18n-inline", "模板条目 2239 条中，语言包已译 460 条"),
    ]
    for expected_rule, text in samples:
        findings = gate.check_text("doc.md", text)
        assert expected_rule in _rules(findings), f"规则 {expected_rule} 没命中样例：{text!r}"


def test_zero_is_not_a_handcopied_count() -> None:
    """「选中 0 个用例必须判红」是规则表述，不是实测值——不得误伤。"""
    findings = gate.check_text("doc.md", "「选中 0 个用例 / 命令数为 0」必须判红")
    assert not findings, findings


# ---------------------------------------------------------------------------
# 反向断言 ①：改生成块一个字符 ⇒ 红
# ---------------------------------------------------------------------------


def test_block_content_drift_is_red(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "README.md",
        "前言\n<!-- BEGIN AUTOGEN:i18n-summary -->\n模板条目 1 条 · 语言包登记 1 条\n"
        "<!-- END AUTOGEN:i18n-summary -->\n",
    )
    findings = gate.check(tmp_path)
    assert "stale-block" in _rules(findings), findings


def test_block_matches_provider_is_green(tmp_path: Path) -> None:
    """把块内容写成 provider 的实测输出 ⇒ 必须绿（否则判据是恒红的）。"""
    expected = gate.PROVIDERS["i18n-summary"]()
    body = "\n".join(expected)
    _write(
        tmp_path,
        "README.md",
        f"<!-- BEGIN AUTOGEN:i18n-summary -->\n{body}\n<!-- END AUTOGEN:i18n-summary -->\n",
    )
    findings = gate.check(tmp_path)
    assert not findings, [f.describe() for f in findings]


# ---------------------------------------------------------------------------
# 反向断言 ②：改真源（算出的数多 1）⇒ 红——证明比的是计算值而非存盘副本
# ---------------------------------------------------------------------------


def test_changing_the_real_source_turns_the_block_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """把 provider 的实测值改大 1，文档没变也必须红。

    这是唯一能证明门禁在比对**当场算出的值**、而不是仓库里那份副本的用例；
    没有它，「读文档里存的数字再和自己比」这种恒真判据也能全绿。
    """
    expected = gate.PROVIDERS["i18n-summary"]()
    _write(
        tmp_path,
        "README.md",
        f"<!-- BEGIN AUTOGEN:i18n-summary -->\n{'\n'.join(expected)}\n"
        "<!-- END AUTOGEN:i18n-summary -->\n",
    )
    assert not gate.check(tmp_path), "基线必须先绿，否则下面的红不说明问题"

    def bumped() -> list[str]:
        return [expected[0] + "（多算一个）"]

    monkeypatch.setitem(gate.PROVIDERS, "i18n-summary", bumped)
    findings = gate.check(tmp_path)
    assert "stale-block" in _rules(findings), findings


def test_update_rewrites_the_block_from_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--update` 必须按 provider 重写，而不是把文档里的旧值当成真源。"""
    monkeypatch.setitem(gate.PROVIDERS, "i18n-summary", lambda: ["唯一正确内容"])
    _write(
        tmp_path,
        "README.md",
        "<!-- BEGIN AUTOGEN:i18n-summary -->\n错的内容\n<!-- END AUTOGEN:i18n-summary -->\n",
    )
    assert gate.update(tmp_path) == ["README.md"]
    assert "唯一正确内容" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert not gate.check(tmp_path)


# ---------------------------------------------------------------------------
# 反向断言 ③：删 BEGIN/END 标记 ⇒ 红
# ---------------------------------------------------------------------------


def test_removing_both_markers_is_red(tmp_path: Path) -> None:
    """把标记删掉、只留裸数字：它就成了手抄，必须被句式扫描抓住。"""
    body = gate.PROVIDERS["i18n-summary"]()
    _write(tmp_path, "README.md", "\n".join(body) + "\n")
    findings = gate.check(tmp_path)
    assert findings, "删掉标记后必须判红（去掉约束不等于数字变成真值）"


def test_removing_only_the_begin_marker_is_red(tmp_path: Path) -> None:
    body = gate.PROVIDERS["i18n-summary"]()
    _write(
        tmp_path,
        "README.md",
        f"{'\n'.join(body)}\n<!-- END AUTOGEN:i18n-summary -->\n",
    )
    findings = gate.check(tmp_path)
    assert "orphan-block-end" in _rules(findings), [f.describe() for f in findings]


def test_removing_only_the_end_marker_is_red(tmp_path: Path) -> None:
    body = gate.PROVIDERS["i18n-summary"]()
    _write(
        tmp_path,
        "README.md",
        f"<!-- BEGIN AUTOGEN:i18n-summary -->\n{'\n'.join(body)}\n",
    )
    findings = gate.check(tmp_path)
    assert "unbalanced-block" in _rules(findings), [f.describe() for f in findings]


def test_mismatched_marker_keys_are_red(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "README.md",
        "<!-- BEGIN AUTOGEN:i18n-summary -->\nx\n<!-- END AUTOGEN:other -->\n",
    )
    findings = gate.check(tmp_path)
    assert "mismatched-block-end" in _rules(findings), [f.describe() for f in findings]


def test_unknown_block_key_is_red(tmp_path: Path) -> None:
    """块标记写错 key ⇒ 既校验不了也重写不了，必须判红而不是静默放过。"""
    _write(
        tmp_path,
        "README.md",
        "<!-- BEGIN AUTOGEN:no-such-provider -->\nx\n<!-- END AUTOGEN:no-such-provider -->\n",
    )
    findings = gate.check(tmp_path)
    assert "unknown-block" in _rules(findings), [f.describe() for f in findings]


# ---------------------------------------------------------------------------
# 反向断言 ④：散文夹带裸数字 ⇒ 红
# ---------------------------------------------------------------------------


def test_handcopied_number_in_prose_is_red(tmp_path: Path) -> None:
    body = gate.PROVIDERS["i18n-summary"]()
    _write(
        tmp_path,
        "CONTRIBUTING.md",
        f"<!-- BEGIN AUTOGEN:i18n-summary -->\n{'\n'.join(body)}\n"
        "<!-- END AUTOGEN:i18n-summary -->\n\n"
        "跑 `pytest tests/gui` 得到 158 passed，可以写上。\n",
    )
    findings = gate.check(tmp_path)
    assert "test-pass-count" in _rules(findings), [f.describe() for f in findings]


def test_generated_block_body_is_exempt_from_phrase_scan(tmp_path: Path) -> None:
    """块内是工具写的，不该被「禁止手抄」的句式扫描误伤——否则生成块永远无法通过。"""
    body = gate.PROVIDERS["i18n-summary"]()
    _write(
        tmp_path,
        "README.md",
        f"<!-- BEGIN AUTOGEN:i18n-summary -->\n{'\n'.join(body)}\n<!-- END AUTOGEN:i18n-summary -->\n",
    )
    assert not gate.check(tmp_path)


# ---------------------------------------------------------------------------
# 反向断言 ⑤：空集合与空块都不能算通过
# ---------------------------------------------------------------------------


def test_no_scanned_document_is_red(tmp_path: Path) -> None:
    findings = gate.check(tmp_path)
    assert "empty-selection" in _rules(findings), findings


def test_no_generated_block_anywhere_is_red(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "只有散文，没有任何生成块。\n")
    findings = gate.check(tmp_path)
    assert "no-blocks" in _rules(findings), [f.describe() for f in findings]


# ---------------------------------------------------------------------------
# 现行仓库
# ---------------------------------------------------------------------------


def test_repository_documents_pass() -> None:
    """这就是它要守的真实对象：现状文档里没有手抄派生数字，生成块与实测一致。"""
    findings = gate.check(_REPO_ROOT)
    assert not findings, [f.describe() for f in findings]


def test_repository_blocks_are_up_to_date() -> None:
    """生成块内容必须已是最新——否则改一行文档就能绕过判据。"""
    scanned, _ = gate.scan_targets(_REPO_ROOT)
    with_blocks = [
        path
        for path in scanned
        if "BEGIN AUTOGEN:" in path.read_text(encoding="utf-8")
    ]
    assert with_blocks, "全仓没有一份文档带生成块，判据已成空转"
    # `update` 返回被改写的文件；空列表即「已是最新」。
    assert gate.update(_REPO_ROOT) == []


def test_gate_is_registered_and_wired() -> None:
    """这条门禁自己也必须进清单并接进 CI，否则它永远不会被执行。"""
    from tools.gate_registry import ci_managed_scripts, gates_for

    registered = set(ci_managed_scripts())
    assert "tools/check_doc_numbers.py" in registered
    names = {gate.name for gate in gates_for(("static",))}
    assert "check_doc_numbers" in names


def test_main_reports_failure_with_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "README.md", "散文里写 158 passed。\n")
    monkeypatch.setattr(sys, "argv", ["check_doc_numbers.py"])
    code = gate.main(["--root", str(tmp_path)])
    assert code == 1
    assert "文档数字一致性检查失败" in capsys.readouterr().err
