"""Contract tests for the coverage gate matchers and ratchet floors (P2-1).

只测**纯逻辑**（matcher / 聚合 / 门槛结构），不跑真实覆盖率——后者由 CI
的 `coverage run` 提供数据，单测里重复采集既慢又不可靠。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_checker() -> ModuleType:
    path = REPO_ROOT / "tools" / "check_coverage_gates.py"
    spec = importlib.util.spec_from_file_location("check_coverage_gates", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_is_one_of_supports_prefix_families() -> None:
    """P1-3 拆分后的模块族用前缀纳入：精确命中与前缀命中都应成立。"""
    checker = _load_checker()
    matcher = checker._is_one_of(
        "src/omnicrawler/security/policy.py",
        prefixes=("src/omnicrawler/state/state_store",),
    )
    assert matcher("src/omnicrawler/security/policy.py")
    assert matcher("src/omnicrawler/state/state_store.py")
    assert matcher("src/omnicrawler/state/state_store_runs.py")
    assert matcher("src/omnicrawler/state/state_store_plugin_state.py")
    assert not matcher("src/omnicrawler/state/capsule_store.py")


def test_coverage_aggregates_by_prefix() -> None:
    """按前缀聚合 covered/statements，并算出百分比。"""
    checker = _load_checker()
    files = {
        # key 同时覆盖两种真实形态：相对路径（本地）与绝对路径（CI runner）
        "src/omnicrawler/state/state_store.py": {
            "summary": {"num_statements": 10, "covered_lines": 5}
        },
        "C:/x/src/omnicrawler/state/state_store_runs.py": {
            "summary": {"num_statements": 30, "covered_lines": 27}
        },
        "C:/x/src/omnicrawler/state/capsule_store.py": {  # 绝对路径形态，应被归一化剔除
            "summary": {"num_statements": 100, "covered_lines": 0}
        },
    }
    covered, statements, percent = checker._coverage(
        files, lambda path: path.startswith("src/omnicrawler/state/state_store")
    )
    assert (covered, statements) == (32, 40)
    assert round(percent, 2) == 80.0


def test_ratchet_floors_are_non_empty_and_bounded() -> None:
    """ratchet 下限必须存在、在合法区间，且按包下限不高于同包关键模块下限之外的值。"""
    checker = _load_checker()
    assert checker.PACKAGE_FLOORS, "P2-1 要求至少覆盖方案点名的 core/state/fetching"
    for package, floor in checker.PACKAGE_FLOORS.items():
        assert 0.0 < floor <= 100.0, (package, floor)
    assert checker.OVERALL_COVERAGE_GATE < 100.0
    for path, floor in checker._FILE_FLOORS.items():
        assert path.startswith("src/omnicrawler/"), path
        assert 0.0 < floor <= 100.0, (path, floor)


def test_browser_and_state_families_stay_in_gate_scope() -> None:
    """P1-3 拆出的模块族必须仍在门禁视野内（防“门面被查、实现漏查”）。"""
    checker = _load_checker()
    probes = {
        "security_and_state": "src/omnicrawler/state/state_store_records.py",
        "browser_and_api": "src/omnicrawler/fetching/browser_engines.py",
    }
    for gate, probe in probes.items():
        _, matcher = checker.GATES[gate]
        assert matcher(probe), f"{gate} 未覆盖 {probe}"
