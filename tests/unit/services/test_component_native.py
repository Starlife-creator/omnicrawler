"""native 组件执行器与 `native` smoke 判据的守卫。

## 这批守卫防什么

`COMPONENT_KINDS` 早就声明了 `native`，但**执行器完全不存在** ——
`component_runner` 只有 `build_wheelpack_spec`（要求 `site-packages/`），
`component_smoke` 也只区分 `data` 与 `wheel-pack`。
于是 `kind: native` 是一个**空转的声明**：能写进配方，但跑不起来。

§15.16 要把浏览器内核（原生可执行文件）纳入组件体系，
必须先补上这条缺失的类型实现。

## 平台差异（实测踩到）

* 真正的 native 组件是**编译好的可执行文件**（`tesseract.exe` / `chromedriver.exe`）；
* **Windows 没有 shebang 机制**：`.py` 不能直接 exec（`WinError 193`），
  测试要用 `.cmd` 包装；
* `os.access(X_OK)` 在 Windows 上不可靠，可执行位检查只在 POSIX 生效。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from omnicrawler.services.component_runner import (
    ComponentRunnerError,
    NativeSpec,
    build_native_spec,
    run_native_component,
)
from omnicrawler.services.component_smoke import SmokeSpecError, parse_smoke

PROBE_PY = (
    "import json, sys\n"
    'payload = json.loads(sys.stdin.read() or "{}")\n'
    'print(json.dumps({"ok": True, "kind": "native", "echo": payload.get("x", 0)}))\n'
)

PROBE_SH = '#!/bin/sh\nexec "{python}" "$(dirname "$0")/probe.py"\n'
PROBE_CMD = '@echo off\n"{python}" "%~dp0probe.py"\n'


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_component(root: Path, *, executable: bool = True) -> dict:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "probe.py").write_text(PROBE_PY, encoding="utf-8")

    if os.name == "nt":
        launcher = bin_dir / "probe.cmd"
        launcher.write_text(PROBE_CMD.format(python=sys.executable), encoding="utf-8")
    else:
        launcher = bin_dir / "probe"
        launcher.write_text(PROBE_SH.format(python=sys.executable), encoding="utf-8")
        if executable:
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    entry = {
        "name": "probe-native",
        "version": "1.0.0",
        "engine": "native",
        "path": f"bin/{launcher.name}",
        "protocol": "probe-v1",
    }
    (root / "entry.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema": 1,
        "name": entry["name"],
        "version": entry["version"],
        "files": {
            "bin/probe.py": _sha256(bin_dir / "probe.py"),
            f"bin/{launcher.name}": _sha256(launcher),
            "entry.json": _sha256(root / "entry.json"),
        },
    }
    (root / "component.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return entry


@pytest.fixture()
def work() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="native-guard-"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_native_component_runs_end_to_end(work: Path) -> None:
    """native 组件能真的 exec 并返回 JSON（协议与 wheel-pack 同形）。"""
    root = work / "probe-native-1.0.0"
    entry = _make_component(root)
    spec = build_native_spec(root, entry)
    assert isinstance(spec, NativeSpec)
    result = run_native_component(spec, {"x": 42})
    assert result["ok"] is True
    assert result["echo"] == 42


def test_native_spec_rejects_path_escape(work: Path) -> None:
    """`entry.path` 逃出组件目录必须被拒（防目录穿越）。"""
    root = work / "escape-1.0.0"
    entry = _make_component(root)
    entry["path"] = "../../../etc/passwd"
    with pytest.raises(ComponentRunnerError) as excinfo:
        build_native_spec(root, entry)
    assert "逃出" in str(excinfo.value)


def test_native_spec_rejects_missing_entry(work: Path) -> None:
    """`entry.path` 为空必须被拒，而不是等到 exec 才崩。"""
    root = work / "noentry-1.0.0"
    entry = _make_component(root)
    entry["path"] = ""
    with pytest.raises(ComponentRunnerError) as excinfo:
        build_native_spec(root, entry)
    assert "缺少 path" in str(excinfo.value)


def test_native_spec_rejects_missing_file(work: Path) -> None:
    """声明的可执行文件不存在时给出可读错误。"""
    root = work / "nofile-1.0.0"
    entry = _make_component(root)
    (root / "bin" / Path(entry["path"]).name).unlink()
    with pytest.raises(ComponentRunnerError) as excinfo:
        build_native_spec(root, entry)
    assert "不存在" in str(excinfo.value)


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有可执行位概念")
def test_native_spec_rejects_missing_exec_bit(work: Path) -> None:
    """POSIX：可执行位丢失必须报可读错误（而非 Permission denied 崩栈）。"""
    root = work / "noexec-1.0.0"
    entry = _make_component(root, executable=False)
    (root / "bin" / "probe").chmod(0o644)
    with pytest.raises(ComponentRunnerError) as excinfo:
        build_native_spec(root, entry)
    assert "执行权限" in str(excinfo.value)


def test_native_smoke_requires_expect() -> None:
    """`kind=native` 必须有 `expect` —— 空期望等于没验。"""
    with pytest.raises(SmokeSpecError):
        parse_smoke({"kind": "native", "smoke": {"expect": []}})


def test_native_smoke_forbids_skip_run() -> None:
    """`kind=native` 不能 `skip_run` —— 它自带可执行入口，必须真的跑一次。"""
    with pytest.raises(SmokeSpecError) as excinfo:
        parse_smoke({"kind": "native", "smoke": {"skip_run": True}})
    assert "native" in str(excinfo.value)


def test_native_smoke_parses_expect() -> None:
    """正常的 native smoke 能解析出 expect。"""
    spec = parse_smoke({"kind": "native", "smoke": {"expect": ["ok", "kind"]}})
    assert spec.expect == ("ok", "kind")
    assert spec.skip_run is False


def test_native_kind_is_declared() -> None:
    """`native` 必须在组件类型清单里（防止有人删了它）。"""
    from omnicrawler.services.component_recipe import COMPONENT_KINDS, component_kind

    assert "native" in COMPONENT_KINDS
    assert component_kind({"kind": "native"}) == "native"

# ── 目录型内置构件（浏览器内核）：装配 → 运行准备全链路 ─────────────────
def test_builtin_directory_component_assembles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**目录型内置构件**（浏览器内核：310 个文件的目录）能装配。

    它的字节留在安装根，装配**不复制**（415 MiB 复制一份既慢又占双倍空间），
    只生成 `component.json` / `entry.json` 两个声明文件。

    实测（2026-11-21）此前有三处会失败：
    1. `_resolve_builtin_source` 只认文件、不认目录；
    2. 装配循环把目录当单文件去 `copyfile`；
    3. 三处无条件要求「入口存在于组装区」，而内置字节不在那里。
    """
    import json

    # 内置构件的 `builtin_path` 相对**安装根**解析。测试把安装根指向
    # tmp_path，否则会去真实安装根找不存在的目录。
    import omnicrawler.core.runtime_paths as rp
    from omnicrawler.services.component_recipe import (
        install_from_recipe,
        load_recipe,
    )

    monkeypatch.setattr(rp, "install_root", lambda: tmp_path)
    monkeypatch.setattr(rp, "browsers_root", lambda: tmp_path / "runtime" / "browsers")

    # 造一个真实的目录型内置构件
    source_dir = tmp_path / "runtime" / "browsers" / "chromium-999"
    (source_dir / "chrome-win64").mkdir(parents=True)
    binary = source_dir / "chrome-win64" / "chrome.exe"
    binary.write_bytes(b"fake-chrome")

    recipe = {
        "schema": 1,
        "id": "probe-builtin",
        "kind": "native",
        "smoke": {"expect": ["ok"]},
        "name": "probe builtin",
        "version": "999",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["x86_64"],
        "python_abi": [],
        "provides": ["browser.probe"],
        "artifacts": [
            {
                "source": "builtin",
                "builtin_path": "runtime/browsers/chromium-999",
                "sha256": "a" * 64,
                "size": len(b"fake-chrome"),
                "format": "raw",
                "filename": "chrome.exe",
            }
        ],
        "entry": {
            "path": "runtime/browsers/chromium-999/chrome-win64/chrome.exe",
            "protocol": "browser-cdp",
            "engine": "chromium",
            "runner": "native",
        },
    }
    recipe_path = tmp_path / "probe-builtin" / "recipe.json"
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")

    loaded = load_recipe(recipe_path)
    root = tmp_path / "components"
    root.mkdir()
    summary = install_from_recipe(loaded, root, work_dir=tmp_path)
    assembled = Path(summary["path"])

    # 安装目录用 id（不是含中文与括号的 name）
    assert assembled.name == "probe-builtin-999", (
        f"安装目录用了 {assembled.name!r} —— 应为 <id>-<version>"
    )
    # 声明文件必须生成，否则装好了也启动不了
    assert (assembled / "entry.json").is_file(), "缺少 entry.json"
    assert (assembled / "component.json").is_file(), "缺少 component.json"
    manifest = json.loads((assembled / "component.json").read_text(encoding="utf-8"))
    assert manifest["external_root"], "目录型内置构件必须声明 external_root"
    # 字节不复制进组件目录
    assert not (assembled / "chrome.exe").exists(), "字节不该被复制"


def test_builtin_component_can_build_native_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """装配后的内置组件能被 `build_native_spec` 接受（受控豁免）。

    `build_native_spec` 有防目录穿越检查，而内置组件的入口在安装根里
    —— 需要 `external_root` 这条受控豁免，且豁免仍受「必须在安装根内」约束。
    """
    import json

    # 内置构件的 `builtin_path` 相对**安装根**解析。测试把安装根指向
    # tmp_path，否则会去真实安装根找不存在的目录。
    import omnicrawler.core.runtime_paths as rp
    from omnicrawler.services.component_recipe import (
        install_from_recipe,
        load_recipe,
    )

    monkeypatch.setattr(rp, "install_root", lambda: tmp_path)
    monkeypatch.setattr(rp, "browsers_root", lambda: tmp_path / "runtime" / "browsers")

    source_dir = tmp_path / "runtime" / "browsers" / "chromium-999"
    entry_dir = source_dir / "chrome-win64"
    entry_dir.mkdir(parents=True)
    entry = entry_dir / "chrome.exe"
    entry.write_bytes(b"fake")
    # **POSIX 上必须给可执行位**：`build_native_spec` 会检查入口是否可执行
    # （`os.access(X_OK)`），否则报「装配应保留可执行位」。
    # Windows 无此概念，跳过。2026-11-22 CI ubuntu/macos 因此红了 4 条。
    if os.name == "posix":
        entry.chmod(0o755)

    recipe = {
        "schema": 1,
        "id": "probe-builtin2",
        "kind": "native",
        "smoke": {"expect": ["ok"]},
        "name": "probe builtin 2",
        "version": "999",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["x86_64"],
        "python_abi": [],
        "provides": ["browser.probe2"],
        "artifacts": [
            {
                "source": "builtin",
                "builtin_path": "runtime/browsers/chromium-999",
                "sha256": "a" * 64,
                "size": 4,
                "format": "raw",
                "filename": "chrome.exe",
            }
        ],
        "entry": {
            "path": "runtime/browsers/chromium-999/chrome-win64/chrome.exe",
            "protocol": "browser-cdp",
            "engine": "chromium",
            "runner": "native",
        },
    }
    recipe_path = tmp_path / "probe-builtin2" / "recipe.json"
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")

    loaded = load_recipe(recipe_path)
    root = tmp_path / "components"
    root.mkdir()
    summary = install_from_recipe(loaded, root, work_dir=tmp_path)
    assembled = Path(summary["path"])
    entry_data = json.loads((assembled / "entry.json").read_text(encoding="utf-8"))

    spec = build_native_spec(assembled, entry_data)
    assert spec.executable.name == "chrome.exe"
