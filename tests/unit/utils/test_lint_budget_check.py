"""Contract tests for the ruff exemption budget gate (P2-2).

门禁本身也要有测试：否则预算文件写错、ruff 调用参数写错都不会被发现，
门禁会静默失效（这类"沉默的门禁"比没有门禁更危险）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_checker() -> ModuleType:
    path = REPO_ROOT / "tools" / "check_lint_budget.py"
    spec = importlib.util.spec_from_file_location("check_lint_budget", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_violations_counts_by_rule_code() -> None:
    """统计结果必须是「rule code → 数量」的映射，且能抓到构造的违规。"""
    checker = _load_checker()
    counts = checker.collect_violations(["src"], ["E402", "E731", "N802", "B904"])
    assert counts, "在 src 下应能统计到被豁免规则的存量违规"
    assert all(code in {"E402", "E731", "N802", "B904", "UNKNOWN"} for code in counts)


def test_budget_file_matches_repository_scope() -> None:
    """预算文件必须可解析、只含 rule code 与计数，且带 _meta.scope。"""
    payload = json.loads((REPO_ROOT / "tools" / "lint-exemption-budget.json").read_text("utf-8"))
    codes = {k: v for k, v in payload.items() if not k.startswith("_")}
    assert codes, "预算文件不应为空"
    assert all(isinstance(v, int) and v >= 0 for v in codes.values())
    scope = (payload.get("_meta") or {}).get("scope")
    assert scope == "src tests", "预算口径需与 CI 的 `ruff check src tests` 一致"


def test_budget_is_zero_slack() -> None:
    """预算应与当前实测一致（零余量）—— 高于实测会让门禁形同虚设。"""
    checker = _load_checker()
    payload = json.loads((REPO_ROOT / "tools" / "lint-exemption-budget.json").read_text("utf-8"))
    budget = {k: int(v) for k, v in payload.items() if not k.startswith("_")}
    actual = checker.collect_violations(["src", "tests"], list(budget))
    slack = {code: budget[code] - actual.get(code, 0) for code in budget}
    assert all(delta == 0 for delta in slack.values()), f"预算存在余量（应同步下调）: {slack}"
