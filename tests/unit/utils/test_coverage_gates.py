"""Contract tests for the coverage gate matchers and ratchet floors (P2-1).

只测**纯逻辑**（matcher / 聚合 / 门槛结构），不跑真实覆盖率——后者由 CI
的 `coverage run` 提供数据，单测里重复采集既慢又不可靠。
"""

from __future__ import annotations

import importlib.util
import json
import sys
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
    # 需浏览器运行时的下限同样要有界、且不得混在通用表里（否则 CI 的 test job 永远不可能过）
    assert checker._BROWSER_FILE_FLOORS, "浏览器专属下限表不应为空（它们是挪走而不是删除）"
    for path, floor in checker._BROWSER_FILE_FLOORS.items():
        assert path.startswith("src/omnicrawler/fetching/browser_"), path
        assert 0.0 < floor <= 100.0, (path, floor)
        assert path not in checker._FILE_FLOORS, f"{path} 应在浏览器专属表里，而不是通用表"


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


# ── 环境档位（2026-09-15）：门禁必须认环境，而不是让不可能达标的下限挡住一切 ──


def test_explicit_profiles_are_returned_verbatim() -> None:
    checker = _load_checker()
    assert checker._resolve_profile("core") == ("core", "显式指定")
    assert checker._resolve_profile("browser") == ("browser", "显式指定")
    assert checker._resolve_profile("full") == ("full", "显式指定")


def test_auto_profile_degrades_honestly() -> None:
    """auto：装了 playwright ⇒ full；没装 ⇒ core（**如实降级并说明理由**，不假装可用）。"""
    checker = _load_checker()
    profile, reason = checker._resolve_profile("auto")
    assert profile in {"core", "full"}, profile
    assert "自动" in reason, reason


def _write_report(path: Path, *, browser_percent: float) -> None:
    """造一份 coverage.json：**两个**浏览器文件按给定覆盖率（其余门禁所需数据本用例不关心）。

    注意：core/full 档还会查分组与按包下限，本样本没有那些文件 ⇒ 那些门禁会报"missing"。
    因此下面断言的是**失败清单里有没有浏览器下限**，而不是整体退出码。
    """
    files = {
        name: {"summary": {"num_statements": 100, "covered_lines": int(browser_percent)}}
        for name in sorted(_browser_floor_paths())
    }
    path.write_text(
        json.dumps(
            {"totals": {"percent_covered": 90.0, "covered_lines": 90, "num_statements": 100}, "files": files}
        ),
        encoding="utf-8",
    )


def _browser_floor_paths() -> tuple[str, ...]:
    return tuple(_load_checker()._BROWSER_FILE_FLOORS)


def _browser_floor_mentioned(text: str) -> bool:
    return any(Path(name).name in text for name in _browser_floor_paths())


def test_core_profile_skips_browser_floors_visibly(tmp_path, monkeypatch, capsys) -> None:
    """core 档（CI 的 test job）：浏览器下限**跳过但要可见**，不得静默、也不得挡路。"""
    checker = _load_checker()
    report = tmp_path / "coverage.json"
    _write_report(report, browser_percent=50.0)  # 远低于 86% ⇒ 若被检查必然出现在失败清单里
    monkeypatch.setattr(sys, "argv", ["check_coverage_gates", str(report), "--profile", "core"])
    checker.main()
    captured = capsys.readouterr()
    assert "已跳过" in captured.out and "profile browser" in captured.out, captured.out
    assert not _browser_floor_mentioned(captured.err), f"core 档不应因浏览器下限失败：{captured.err}"


def test_browser_profile_enforces_only_browser_floors(tmp_path, monkeypatch, capsys) -> None:
    """browser 档：只查浏览器专属下限（该 job 只跑浏览器子集，总/分组不适用）。"""
    checker = _load_checker()

    bad = tmp_path / "bad.json"
    _write_report(bad, browser_percent=50.0)
    monkeypatch.setattr(sys, "argv", ["check_coverage_gates", str(bad), "--profile", "browser"])
    assert checker.main() == 1, "browser 档必须抓住浏览器文件覆盖率不足"
    assert _browser_floor_mentioned(capsys.readouterr().err)

    ok = tmp_path / "ok.json"
    _write_report(ok, browser_percent=95.0)
    monkeypatch.setattr(sys, "argv", ["check_coverage_gates", str(ok), "--profile", "browser"])
    assert checker.main() == 0, "达到下限时应通过"
    out = capsys.readouterr().out
    # 只打印浏览器下限、不应出现分组/包门禁行
    assert "pdf_and_ocr" not in out and "desktop_core" not in out and "pkg:" not in out, out


def test_full_profile_checks_everything(tmp_path, monkeypatch, capsys) -> None:
    """full 档（本地装了 browser extras）：浏览器下限也必须被检查。"""
    checker = _load_checker()
    report = tmp_path / "coverage.json"
    _write_report(report, browser_percent=50.0)
    monkeypatch.setattr(sys, "argv", ["check_coverage_gates", str(report), "--profile", "full"])
    assert checker.main() == 1
    captured = capsys.readouterr()
    assert _browser_floor_mentioned(captured.err), captured.err
    assert "已跳过" not in captured.out, "full 档不应跳过浏览器下限"
