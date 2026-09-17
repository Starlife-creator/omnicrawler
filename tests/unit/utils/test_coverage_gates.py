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
    assert checker._resolve_profile("core") == ("core", "explicit")
    assert checker._resolve_profile("browser") == ("browser", "explicit")
    assert checker._resolve_profile("full") == ("full", "explicit")


def test_auto_profile_degrades_honestly() -> None:
    """auto：装了 playwright ⇒ full；没装 ⇒ core（**如实降级并说明理由**，不假装可用）。"""
    checker = _load_checker()
    profile, reason = checker._resolve_profile("auto")
    assert profile in {"core", "full"}, profile
    assert reason.startswith("auto:"), reason


def test_prints_survive_a_cp1252_console(tmp_path, monkeypatch) -> None:
    """**打印必须是 ASCII 安全的**（2026-09-15 实测：Windows runner 的 stdout 是 cp1252）。

    `quality` 的 windows job 曾直接崩在
    `UnicodeEncodeError: 'charmap' codec can't encode characters`——本脚本在三个平台打日志，
    只要有一句带全角括号就会炸。用 cp1252 编码试打四种档位，把「只在 Windows CI 上炸」
    变成**本地也能验**的检查（与「把依赖镜像才崩转成与镜像无关的断言」同一思路）。
    """
    import contextlib
    import io

    checker = _load_checker()
    report = tmp_path / "coverage.json"
    _write_report(report, browser_percent=_percent_above_all_browser_floors())

    for profile in ("core", "browser", "full", "auto"):
        monkeypatch.setattr(
            sys, "argv", ["check_coverage_gates", str(report), "--profile", profile]
        )
        buffer = io.BytesIO()
        with contextlib.redirect_stdout(io.TextIOWrapper(buffer, encoding="cp1252", write_through=True)):
            checker.main()  # 有任何非 ASCII 打印都会在这里抛 UnicodeEncodeError


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


def _percent_below_all_browser_floors() -> float:
    """**从下限派生**取一个必然不达标的覆盖率（不要把数字写死：下限会随实测校准而变）。"""
    return min(_load_checker()._BROWSER_FILE_FLOORS.values()) - 10.0


def _percent_above_all_browser_floors() -> float:
    return max(_load_checker()._BROWSER_FILE_FLOORS.values()) + 5.0


def _browser_floor_mentioned(text: str) -> bool:
    return any(Path(name).name in text for name in _browser_floor_paths())


def test_core_profile_skips_browser_floors_visibly(tmp_path, monkeypatch, capsys) -> None:
    """core 档（CI 的 test job）：浏览器下限**跳过但要可见**，不得静默、也不得挡路。"""
    checker = _load_checker()
    report = tmp_path / "coverage.json"
    _write_report(report, browser_percent=_percent_below_all_browser_floors())  # 必然低于任何下限
    monkeypatch.setattr(sys, "argv", ["check_coverage_gates", str(report), "--profile", "core"])
    checker.main()
    captured = capsys.readouterr()
    assert "skipped: needs browser" in captured.out, captured.out
    assert "profile browser" in captured.out, captured.out
    assert not _browser_floor_mentioned(captured.err), f"core 档不应因浏览器下限失败：{captured.err}"


def test_browser_profile_enforces_only_browser_floors(tmp_path, monkeypatch, capsys) -> None:
    """browser 档：只查浏览器专属下限（该 job 只跑浏览器子集，总/分组不适用）。"""
    checker = _load_checker()

    bad = tmp_path / "bad.json"
    _write_report(bad, browser_percent=_percent_below_all_browser_floors())
    monkeypatch.setattr(sys, "argv", ["check_coverage_gates", str(bad), "--profile", "browser"])
    assert checker.main() == 1, "browser 档必须抓住浏览器文件覆盖率不足"
    assert _browser_floor_mentioned(capsys.readouterr().err)

    ok = tmp_path / "ok.json"
    _write_report(ok, browser_percent=_percent_above_all_browser_floors())
    monkeypatch.setattr(sys, "argv", ["check_coverage_gates", str(ok), "--profile", "browser"])
    assert checker.main() == 0, "达到下限时应通过"
    out = capsys.readouterr().out
    # 只打印浏览器下限、不应出现分组/包门禁行
    assert "pdf_and_ocr" not in out and "desktop_core" not in out and "pkg:" not in out, out


def test_full_profile_checks_everything(tmp_path, monkeypatch, capsys) -> None:
    """full 档（本地装了 browser extras）：浏览器下限也必须被检查。"""
    checker = _load_checker()
    report = tmp_path / "coverage.json"
    _write_report(report, browser_percent=_percent_below_all_browser_floors())
    monkeypatch.setattr(sys, "argv", ["check_coverage_gates", str(report), "--profile", "full"])
    assert checker.main() == 1
    captured = capsys.readouterr()
    assert _browser_floor_mentioned(captured.err), captured.err
    assert "已跳过" not in captured.out, "full 档不应跳过浏览器下限"

# --- W6.2 (2026-09-17): 覆盖率基线 只升不降 的机检守卫 --------------------------
#
# 由来：方案 W6.2 判据是「基线单调不降」，但此前只靠注释写一句约定
# （收紧后要把数字写回文件）—— 属典型的「依赖人记得去做」，正是可复用资产判据的反例。
# 这里把它变成会失败的检查。
#
# 取值来源：quality 全绿首个 run（3ed2df9，2026-09-17）三平台实测最小值减 2。
# 收紧基线时要同时抬高这里的快照；放松任何一项都会让本用例变红。
_FLOORS_SNAPSHOT_2026_09_17: dict[str, float] = {
    "OVERALL_COVERAGE_GATE": 73.6,
    "GATES.security_and_state": 90.0,
    "GATES.pipeline_http_sources": 81.0,
    "GATES.pipeline_http_client": 86.4,
    "GATES.browser_and_api": 74.8,
    "GATES.pdf_and_ocr": 72.6,
    "GATES.desktop_core": 78.4,
    "PACKAGE_FLOORS.core": 87.0,
    "PACKAGE_FLOORS.state": 92.0,
    "PACKAGE_FLOORS.fetching": 73.0,
    "_FILE_FLOORS.policy.py": 84.0,
    "_FILE_FLOORS.egress.py": 89.0,
    "_FILE_FLOORS.http_client.py": 82.0,
    "_FILE_FLOORS.plugins.py": 98.0,
    "_FILE_FLOORS.state_store_records.py": 97.0,
    "_FILE_FLOORS.state_store_runs.py": 85.0,
    "_FILE_FLOORS.plugin_loader.py": 82.0,
    "_FILE_FLOORS.plugin_market_install.py": 30.0,
    "_FILE_FLOORS.pdf_workbench_worker.py": 18.0,
    "_FILE_FLOORS.field_extractor.py": 34.0,
    "_FILE_FLOORS.pdf_processor.py": 42.0,
    "_BROWSER_FILE_FLOORS.browser_engines.py": 41.0,
    "_BROWSER_FILE_FLOORS.browser_pool.py": 44.0,
}


def _current_floors(checker: ModuleType) -> dict[str, float]:
    floors = {"OVERALL_COVERAGE_GATE": checker.OVERALL_COVERAGE_GATE}
    floors.update({f"GATES.{name}": value for name, (value, _m) in checker.GATES.items()})
    floors.update({f"PACKAGE_FLOORS.{k}": v for k, v in checker.PACKAGE_FLOORS.items()})
    floors.update({f"_FILE_FLOORS.{Path(k).name}": v for k, v in checker._FILE_FLOORS.items()})
    floors.update(
        {f"_BROWSER_FILE_FLOORS.{Path(k).name}": v for k, v in checker._BROWSER_FILE_FLOORS.items()}
    )
    return floors


def test_coverage_ratchet_never_lowers_a_floor() -> None:
    """基线只升不降：任何一项被调低，或某个门禁被删掉，都必须判红。"""
    current = _current_floors(_load_checker())
    missing = sorted(set(_FLOORS_SNAPSHOT_2026_09_17) - set(current))
    assert not missing, (
        f"这些覆盖率门禁被删除了 —— 删门禁等于放宽口径，与 W6.2 只升不降相悖：{missing}"
    )
    lowered = {k: (v, current[k]) for k, v in _FLOORS_SNAPSHOT_2026_09_17.items() if current[k] < v}
    assert not lowered, f"这些覆盖率下限被调低了（快照->现值）：{lowered}"
