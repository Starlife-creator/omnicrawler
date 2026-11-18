"""Standard 全装齐 ↔ Full 的能力等价性对账。

## 为什么这条对账必须存在

维护者的要求是「假如说有人全部安装并加载了，那么和现在的 Full 版一模一样」。
停发 Full 之后，这句话不能只是声明——必须能被验证。Full 继续在 CI 里构建，
正是为了当这个基准。

## 判据

1. Full 有、Standard 全装齐也有 ⇒ 等价（目标状态）；
2. Full 有、Standard 没有 ⇒ **缺口**，判失败（有东西没被组件化）；
3. Standard 有、Full 没有 ⇒ 只警告（可能基准该更新，不一定是坏事）；
4. **空集合必须判失败**——否则所有断言恒真；
5. 只比较**就绪**的能力项：Full 里本就未就绪的不要求 Standard 有。

## 明确边界

本文件验证的是**对账逻辑本身**（纯函数 + CLI 接线），用构造的报告数据。
真实等价性需要 CI 跑完三平台构建后，拿实际产物报告来比——那一步本机做不到。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_EQUIV = _REPO / "tools" / "portable" / "standard_equivalence.py"
_SMOKE = _REPO / "tools" / "portable" / "portable_smoke_test.py"

pytestmark = pytest.mark.skipif(not _EQUIV.is_file(), reason="需要 tools/portable")


def _load_equivalence():
    """按路径加载对账模块（与 smoke test 的做法一致）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_equiv_under_test", _EQUIV)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def equiv():
    return _load_equivalence()


def _report(standard: dict[str, bool], full: dict[str, bool] | None = None) -> dict:
    return {
        "standard": {"items": {k: {"ready": v} for k, v in standard.items()}},
        "full": {"items": {k: {"ready": v} for k, v in (full or {}).items()}},
        "optional": {"items": {}},
    }


def test_identical_reports_are_equivalent(equiv) -> None:
    report = equiv.compare_capability_sets(
        _report({"a": True, "b": True}),
        _report({"a": True, "b": True}),
    )
    assert report.ok
    assert report.missing_in_standard == ()
    assert report.checked == 2


def test_missing_capability_is_a_failure(equiv) -> None:
    """Full 有、Standard 没有 ⇒ 缺口，必须判失败。"""
    report = equiv.compare_capability_sets(
        _report({"a": True, "b": True}),
        _report({"a": True}),
    )
    assert not report.ok
    assert report.missing_in_standard == ("standard.b",)


def test_extra_capability_is_only_a_warning(equiv) -> None:
    """Standard 比 Full 多不算失败，但要被看见。"""
    report = equiv.compare_capability_sets(
        _report({"a": True}),
        _report({"a": True, "c": True}),
    )
    assert report.ok, "超出基准不该判失败"
    assert report.beyond_full == ("standard.c",)


def test_empty_baseline_is_a_failure(equiv) -> None:
    """空集合会让所有断言恒真 —— 必须显式判失败。"""
    report = equiv.compare_capability_sets(_report({}), _report({"a": True}))
    assert not report.ok
    assert report.note, "空基准必须给出原因"


def test_unreadable_report_is_a_failure(equiv) -> None:
    assert not equiv.compare_capability_sets(None, _report({"a": True})).ok
    assert not equiv.compare_capability_sets(_report({"a": True}), None).ok


def test_only_ready_items_are_compared(equiv) -> None:
    """Full 里本就未就绪的项，不要求 Standard 有。"""
    report = equiv.compare_capability_sets(
        _report({"a": True, "b": False}),
        _report({"a": True}),
    )
    assert report.ok, "把 Full 里未就绪的项也算成缺口会误判"


def test_sections_do_not_collide(equiv) -> None:
    """不同段里的同名键不能被覆盖。"""
    full = {
        "standard": {"items": {"x": {"ready": True}}},
        "optional": {"items": {"x": {"ready": True}}},
        "full": {"items": {}},
    }
    standard = {
        "standard": {"items": {"x": {"ready": True}}},
        "optional": {"items": {"x": {"ready": False}}},
        "full": {"items": {}},
    }
    report = equiv.compare_capability_sets(full, standard)
    assert not report.ok, "optional.x 未就绪应被检出，不能被 standard.x 掩盖"
    assert "optional.x" in report.missing_in_standard


def test_render_report_mentions_gap(equiv) -> None:
    report = equiv.compare_capability_sets(_report({"a": True, "b": True}), _report({"a": True}))
    text = equiv.render_report(report)
    assert "缺" in text
    assert "standard.b" in text


def test_cli_returns_zero_on_equivalence(tmp_path: Path) -> None:
    """CLI 在等价时退出码 0。"""
    payload = _report({"a": True})
    full = tmp_path / "full.json"
    standard = tmp_path / "std.json"
    for path in (full, standard):
        path.write_text(json.dumps(payload), encoding="utf-8")

    env = {"PYTHONPATH": str(_REPO / "src")}
    result = subprocess.run(
        [sys.executable, str(_SMOKE), "--compare-capabilities", str(full), str(standard)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **env},
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_returns_nonzero_on_gap(tmp_path: Path) -> None:
    """CLI 在有缺口时退出码非 0 —— CI 才能据此判红。"""
    full = tmp_path / "full.json"
    standard = tmp_path / "std.json"
    full.write_text(json.dumps(_report({"a": True, "b": True})), encoding="utf-8")
    standard.write_text(json.dumps(_report({"a": True})), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_SMOKE), "--compare-capabilities", str(full), str(standard)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(_REPO / "src")},
        timeout=120,
        check=False,
    )
    assert result.returncode != 0, "有缺口却退出 0 —— CI 会放过真正的等价性破坏"


def test_cli_returns_nonzero_on_unreadable_input(tmp_path: Path) -> None:
    """输入不可读时退出码应是 2（区别于"有缺口"的 1）。"""
    import os

    missing = tmp_path / "nope.json"
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_report({"a": True})), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_SMOKE), "--compare-capabilities", str(missing), str(ok)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(_REPO / "src")},
        timeout=120,
        check=False,
    )
    assert result.returncode == 2, f"应返回 2（输入错误），实际 {result.returncode}"

# ── 能力报告收集脚本 ─────────────────────────────────────────────────────
_COLLECT = _REPO / "tools" / "portable" / "collect_capability_reports.py"


def _load_collector():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_collect_under_test", _COLLECT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not _COLLECT.is_file(), reason="需要收集脚本")
def test_collector_finds_both_editions(tmp_path: Path) -> None:
    """能从解压目录里按 EDITION.txt 找出两个 edition。"""
    collect = _load_collector()
    for edition in ("Standard", "Full"):
        directory = tmp_path / "OmniCrawler-1.0.2-win" / edition
        directory.mkdir(parents=True)
        (directory / "EDITION.txt").write_text(edition + "\n", encoding="utf-8")
        (directory / "omnicrawler-cli.exe").write_text("", encoding="utf-8")

    roots = collect.find_release_roots(tmp_path)
    assert set(roots) == {"standard", "full"}
    assert roots["full"].name == "Full"
    assert roots["standard"].name == "Standard"


@pytest.mark.skipif(not _COLLECT.is_file(), reason="需要收集脚本")
def test_collector_handles_edition_prefix(tmp_path: Path) -> None:
    """`Edition: Full` 这种写法也要认得（不同脚本产出的格式可能不同）。"""
    collect = _load_collector()
    directory = tmp_path / "pkg" / "Full"
    directory.mkdir(parents=True)
    (directory / "EDITION.txt").write_text("Edition: Full\n", encoding="utf-8")

    roots = collect.find_release_roots(tmp_path)
    assert "full" in roots


@pytest.mark.skipif(not _COLLECT.is_file(), reason="需要收集脚本")
def test_collector_missing_edition_warns_not_silently_passes(tmp_path: Path) -> None:
    """缺某个 edition 时必须输出 warning，不能静默通过。

    静默通过比失败更危险：对账步骤会以为"等价"。
    """
    collect = _load_collector()
    directory = tmp_path / "pkg" / "Full"
    directory.mkdir(parents=True)
    (directory / "EDITION.txt").write_text("Full\n", encoding="utf-8")
    (directory / "omnicrawler-cli.exe").write_text("", encoding="utf-8")

    out = tmp_path / "out"
    code = collect.main([str(tmp_path), str(out)])
    # 缺 edition ⇒ 对账无法进行 ⇒ 必须返回非 0（早期版本这里返回 0，
    # 导致 CI 拿缺一份报告去对账并得出误导性结论）。
    assert code == 3, f"缺 edition 应返回 3（无法对账），实际 {code}"
    assert not (out / "cap-standard.json").exists()


@pytest.mark.skipif(not _COLLECT.is_file(), reason="需要收集脚本")
def test_collector_locate_executable_prefers_exe(tmp_path: Path) -> None:
    """三平台可执行文件候选名都要能识别。"""
    collect = _load_collector()
    for name in ("omnicrawler-cli.exe", "omnicrawler-cli", "omnicrawler"):
        root = tmp_path / name
        root.mkdir()
        (root / name).write_text("", encoding="utf-8")
        assert collect.locate_executable(root) is not None, f"未识别 {name}"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert collect.locate_executable(empty) is None

# ── EDITION.txt 真实格式（构建脚本核实） ─────────────────────────────────
@pytest.mark.skipif(not _COLLECT.is_file(), reason="需要收集脚本")
def test_edition_marker_handles_real_build_output(tmp_path: Path) -> None:
    """必须认得构建脚本实际写出的整句格式。

    三个平台的构建脚本写的是：

        OmniCrawler Full portable edition
        OmniCrawler Standard portable edition

    而**不是**裸的 `Full`。早先按精确匹配判断会永远失败，导致等价性对账
    在真实产物上**静默跳过**（实测核实 build_windows.ps1 / build_linux.sh /
    build_macos.sh 后才发现）。
    """
    collect = _load_collector()

    assert collect.edition_from_marker("OmniCrawler Full portable edition") == "full"
    assert collect.edition_from_marker("OmniCrawler Standard portable edition") == "standard"

    # 也要兼容裸值与前缀写法
    assert collect.edition_from_marker("Full\n") == "full"
    assert collect.edition_from_marker("Edition: Standard") == "standard"

    # 认不出来时返回空串，不能瞎猜
    assert collect.edition_from_marker("") == ""
    assert collect.edition_from_marker("某个无关内容") == ""


@pytest.mark.skipif(not _COLLECT.is_file(), reason="需要收集脚本")
def test_find_roots_with_real_marker_content(tmp_path: Path) -> None:
    """端到端：用真实格式的 EDITION.txt 定位两个 release root。"""
    collect = _load_collector()
    for edition in ("Standard", "Full"):
        directory = tmp_path / "pkg" / edition
        directory.mkdir(parents=True)
        (directory / "EDITION.txt").write_text(
            f"OmniCrawler {edition} portable edition", encoding="utf-8"
        )
        (directory / "omnicrawler-cli").write_text("", encoding="utf-8")

    roots = collect.find_release_roots(tmp_path)
    assert set(roots) == {"standard", "full"}, (
        f"用真实格式未能识别两个 edition：{sorted(roots)}"
    )


def test_build_scripts_still_write_the_expected_marker() -> None:
    """构建脚本写出的格式若变了，收集脚本要同步——这条守卫盯住契约。

    它不是"验证构建"，而是"验证解析假设与构建脚本一致"。
    """
    windows = _REPO / "build_windows.ps1"
    linux = _REPO / "build_linux.sh"
    macos = _REPO / "build_macos.sh"

    found = 0
    for script in (windows, linux, macos):
        if not script.is_file():
            continue
        text = script.read_text(encoding="utf-8", errors="replace")
        # 三个脚本都写成 `"OmniCrawler $Edition portable edition"`（PS 用 $Edition，
        # shell 用 $EDITION）；`portable edition` 这个尾缀是关键契约。
        if "portable edition" in text and "OmniCrawler $" in text:
            found += 1

    assert found >= 2, (
        "构建脚本里的 EDITION.txt 写法变了（未找到 "
        '"OmniCrawler $Edition portable edition"），收集脚本的解析需要同步'
    )

# ── 两个 edition 共存时的解压与定位 ─────────────────────────────────────
_ARCHIVE_SMOKE = _REPO / "tools" / "portable" / "portable_archive_smoke.py"


def _load_archive_smoke():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_archive_smoke_under_test", _ARCHIVE_SMOKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not _ARCHIVE_SMOKE.is_file(), reason="需要归档冒烟脚本")
def test_find_archive_filters_by_edition(tmp_path: Path) -> None:
    """同一目录下两个 edition 必须能被区分。

    不带 edition 过滤时永远取字典序靠前的那个（`Full` < `Standard`），
    于是"验证 Standard"实际验的是 Full —— 等价性对账拿到两份相同报告，
    永远报"完全等价"。
    """
    import zipfile

    archive = _load_archive_smoke()
    for edition in ("Standard", "Full"):
        name = f"OmniCrawler-1.0.2-Windows-Portable-{edition}.zip"
        with zipfile.ZipFile(tmp_path / name, "w") as zf:
            zf.writestr("OmniCrawler/EDITION.txt", f"OmniCrawler {edition} portable edition")

    standard = archive.find_archive(tmp_path, "windows", edition="Standard")
    full = archive.find_archive(tmp_path, "windows", edition="Full")

    assert "Standard" in standard.name, f"未取到 Standard：{standard.name}"
    assert "Full" in full.name, f"未取到 Full：{full.name}"
    assert standard != full, "两个 edition 取到了同一个归档"


@pytest.mark.skipif(not _ARCHIVE_SMOKE.is_file(), reason="需要归档冒烟脚本")
def test_find_archive_without_edition_keeps_old_behaviour(tmp_path: Path) -> None:
    """不指定 edition 时保持原行为（取第一个命中）。"""
    import zipfile

    archive = _load_archive_smoke()
    name = "OmniCrawler-1.0.2-Windows-Portable-Full.zip"
    with zipfile.ZipFile(tmp_path / name, "w") as zf:
        zf.writestr("OmniCrawler/EDITION.txt", "OmniCrawler Full portable edition")

    found = archive.find_archive(tmp_path, "windows")
    assert found.name == name


@pytest.mark.skipif(not _ARCHIVE_SMOKE.is_file(), reason="需要归档冒烟脚本")
def test_missing_edition_raises_with_names(tmp_path: Path) -> None:
    """指定了不存在的 edition 时报错要说明找的是什么。"""
    import zipfile

    archive = _load_archive_smoke()
    with zipfile.ZipFile(tmp_path / "OmniCrawler-1.0.2-Windows-Portable-Full.zip", "w") as zf:
        zf.writestr("x", "")

    with pytest.raises(FileNotFoundError) as excinfo:
        archive.find_archive(tmp_path, "windows", edition="Standard")
    assert "Standard" in str(excinfo.value)


def test_release_yml_extracts_both_editions() -> None:
    """CI 必须同时解压两个 edition，否则对账拿不到第二份报告。"""
    release = _REPO / ".github" / "workflows" / "release.yml"
    text = release.read_text(encoding="utf-8")
    assert "--also-extract-editions" in text, (
        "CI 未请求额外解压 Standard —— 等价性对账会因找不到两份报告而静默跳过"
    )

# ── native 段必须参与对账 ────────────────────────────────────────────────
def test_native_section_is_compared(equiv) -> None:
    """顶层 `native` 段必须参与对账。

    这一段装的正是 Full 相对 Standard 多出来的东西：
    `chromium` / `selenium_driver` / `tesseract` / `paddle_models`。
    早先只对账三段 `items`，等于把「全装齐后这些原生能力是否齐备」
    这个核心问题整个跳过了。
    """
    full = {
        "native": {
            "chromium": {"ready": True},
            "tesseract": {"ready": True},
        }
    }
    standard = {
        "native": {
            "chromium": {"ready": False},  # 缺 chromium
            "tesseract": {"ready": True},
        }
    }

    report = equiv.compare_capability_sets(full, standard)
    assert not report.ok, "native 段的缺口未被检出 —— 对账等于跳过了核心能力"
    assert "native.chromium" in report.missing_in_standard
    assert "native.tesseract" not in report.missing_in_standard, "已就绪的项不该误报"


def test_native_section_uses_flat_structure(equiv) -> None:
    """`native` 是扁平结构（key 直接是能力名），不是 `{items: {...}}`。

    两种结构混用会让解析静默漏项 —— 所以单独钉住这个形状。
    """
    report = {
        "native": {"chromium": {"ready": True}, "paddle_models": {"ready": True}},
        "standard": {"items": {}},
        "full": {"items": {}},
        "optional": {"items": {}},
    }
    collected = equiv._collect_ready(report)
    assert "native.chromium" in collected
    assert "native.paddle_models" in collected
    assert len([k for k in collected if k.startswith("native.")]) == 2


def test_native_missing_key_is_not_counted(equiv) -> None:
    """`native` 项若没有 ready 字段，不该被当成"已就绪"。"""
    report = {"native": {"weird": {"path": "x"}}}
    collected = equiv._collect_ready(report)
    assert "native.weird" not in collected


def test_real_capability_report_covers_native() -> None:
    """用**真实** capability_report 验证 native 段被覆盖（不只测构造数据）。"""
    from omnicrawler.core.capabilities import capability_report

    report = capability_report(mode="quick")
    assert isinstance(report.get("native"), dict), "真实报告里没有 native 段"

    equiv = _load_equivalence()
    collected = equiv._collect_ready(report)
    native_keys = [k for k in collected if k.startswith("native.")]
    assert native_keys, (
        "真实 capability_report 的 native 段一项都没被收集 —— "
        "对账会漏掉 chromium/tesseract/paddle_models 这些核心原生能力"
    )

# ── 收集失败必须显式非 0，不能拿空报告去对账 ─────────────────────────────
@pytest.mark.skipif(not _COLLECT.is_file(), reason="需要收集脚本")
def test_collector_returns_nonzero_when_executable_missing(tmp_path: Path) -> None:
    """没有可执行文件时返回 3，而不是 0。

    早先这里只打 `::warning::` 然后 return 0，CI 会继续拿缺一份报告去对账，
    得出「Standard 缺 N 项能力」这种**误导性结论**——真实原因是能力报告
    根本没读出来。
    """
    collect = _load_collector()
    root = tmp_path / "Full" / "OmniCrawler"
    root.mkdir(parents=True)
    (root / "EDITION.txt").write_text("OmniCrawler Full portable edition", encoding="utf-8")

    code = collect.main([str(tmp_path), str(tmp_path / "out")])
    assert code == 3, f"应返回 3（无法对账），实际 {code}"
    assert not (tmp_path / "out" / "cap-full.json").exists(), (
        "探测失败却写出了报告文件 —— 空报告会被当成「该 edition 零能力」"
    )


@pytest.mark.skipif(not _COLLECT.is_file(), reason="需要收集脚本")
def test_probe_failure_is_not_written_as_empty_report(tmp_path: Path) -> None:
    """探测返回空 dict 时必须报错，不能写 `{}` 当成有效报告。"""
    collect = _load_collector()
    root = tmp_path / "Full" / "OmniCrawler"
    root.mkdir(parents=True)
    (root / "EDITION.txt").write_text("OmniCrawler Full portable edition", encoding="utf-8")
    # 有个"可执行文件"但不是真的可执行 —— probe 会失败返回空 dict
    (root / "omnicrawler-cli").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    code = collect.main([str(tmp_path), str(tmp_path / "out")])
    if code == 0:
        # 若环境恰好能跑起来，至少要写出非空报告
        payload = json.loads((tmp_path / "out" / "cap-full.json").read_text(encoding="utf-8"))
        assert payload, "写了空报告却返回成功"
    else:
        assert code == 3, f"探测失败应返回 3，实际 {code}"


def test_ci_stops_when_collection_fails() -> None:
    """CI 步骤必须在收集失败时停止，不能继续拿残缺报告对账。"""
    release = _REPO / ".github" / "workflows" / "release.yml"
    text = release.read_text(encoding="utf-8")
    assert "collect_code" in text, "CI 未检查收集脚本退出码"
    assert 'exit "$collect_code"' in text, "CI 未在收集失败时中止"
