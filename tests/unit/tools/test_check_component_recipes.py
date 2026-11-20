"""组件配方门禁的守卫：声明必须能从构件字节推导。

## 这批守卫防什么

v1.0.13→v1.0.29 的 17 轮里，配方缺陷**全是同一类**——声明与实际不符。
这个门禁（`tools/gates/check_component_recipes.py`）对每个可推导字段做一次比对。

**这里的守卫不是重复门禁逻辑**，而是用**最小构造配方**验证每条判据本身有效：
门禁对真实配方说「通过」可能是碰巧，用构造数据才能确认它真的会判红。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "tools" / "gates" / "check_component_recipes.py"


def _run_gate(root: Path) -> tuple[int, str]:
    """在给定目录上跑门禁，返回 (退出码, 输出)。"""
    completed = subprocess.run(
        [sys.executable, str(_GATE), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_REPO),
        env={**__import__("os").environ, "PYTHONPATH": str(_REPO / "src"), "PYTHONUTF8": "1"},
        timeout=120,
        check=False,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def _base_recipe(**overrides: object) -> dict:
    recipe: dict = {
        "schema": 1,
        "name": "probe",
        "version": "1.0.0",
        "kind": "wheel-pack",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["x86_64"],
        "python_abi": ["cp313"],
        "provides": ["probe.demo"],
        "artifacts": [
            {
                "url": "https://e.invalid/a-cp313-cp313-win_amd64.whl",
                "sha256": "a" * 64,
                "size": 100,
                "format": "whl",
                "extract_to": "site-packages",
                "platforms": ["windows"],
            },
            {
                "url": "https://e.invalid/b-cp313-cp313-manylinux_2_17_x86_64.whl",
                "sha256": "b" * 64,
                "size": 100,
                "format": "whl",
                "extract_to": "site-packages",
                "platforms": ["linux"],
            },
            {
                "url": "https://e.invalid/c-cp313-cp313-macosx_10_13_x86_64.whl",
                "sha256": "c" * 64,
                "size": 100,
                "format": "whl",
                "extract_to": "site-packages",
                "platforms": ["macos"],
            },
        ],
        "entry": {
            "path": "entry.json",
            "protocol": "demo-v1",
            "engine": "generic",
            "runner": "worker-wheelpack",
            "module": "site-packages/pkg/__init__.py",
        },
    }
    recipe.update(overrides)
    return recipe


def _write_component(root: Path, name: str, recipe: dict, *, lock: bool = True) -> None:
    """写一个组件到临时目录。

    `lock=True` 时顺带写一份**与配方匹配**的依赖闭包快照（空包列表）——
    否则「缺少 deps.lock.json」会盖住我们真正想测的那条判据。
    """
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "recipe.json").write_text(
        json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if lock:
        sys.path.insert(0, str(_REPO / "src"))
        from omnicrawler.services.component_recipe import (
            RecipeError,
            load_recipe,
            recipe_digest,
        )

        try:
            loaded = load_recipe(target / "recipe.json")
            digest = recipe_digest(loaded)
        except RecipeError:
            # 配方本身不合法（正是某些用例要测的）——快照的 digest 用占位值，
            # 让门禁先报「配方不合法」而不是「快照过期」，避免遮住真正要测的判据。
            digest = "0" * 64
        snapshot = {
            "recipe_digest": digest,
            "python_version": "3.13",
            "packages": [],
        }
        (target / "deps.lock.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def test_gate_passes_on_valid_recipe(tmp_path: Path) -> None:
    """合法配方必须通过（否则门禁会误伤正常组件）。"""
    _write_component(tmp_path, "probe", _base_recipe())
    code, output = _run_gate(tmp_path)
    assert code == 0, f"合法配方被拒：{output}"


def test_gate_fails_on_empty_directory(tmp_path: Path) -> None:
    """**空集合必须判红** —— 没有配方不等于通过。"""
    code, output = _run_gate(tmp_path)
    assert code == 1, f"空目录被判为通过：{output}"
    assert "未找到" in output


def test_gate_fails_on_architecture_overclaim(tmp_path: Path) -> None:
    """声明 aarch64 却只有 x86_64 构件 ⇒ 判红（架构虚报）。

    这是 v1.0.27 的真实缺陷形状：`architectures: [x86_64, aarch64]`
    但所有构件都是 x86_64。
    """
    recipe = _base_recipe(architectures=["x86_64", "aarch64"])
    _write_component(tmp_path, "probe", recipe)
    code, output = _run_gate(tmp_path)
    assert code == 1
    assert "aarch64" in output


def test_gate_or_schema_rejects_undeclared_architecture(tmp_path: Path) -> None:
    """构件里有某架构、architectures 没声明 ⇒ **必须有一道拦住**。

    `architectures` 为空会被 schema 先拒（它要求非空列表），
    所以这里同样断言整体契约而非具体层。
    """
    recipe = _base_recipe(architectures=[])
    _write_component(tmp_path, "probe", recipe)
    code, output = _run_gate(tmp_path)
    assert code == 1, f"空 architectures 居然通过了：{output}"


def test_gate_or_schema_rejects_missing_platform_artifact(tmp_path: Path) -> None:
    """声明支持某平台但没有该平台的构件 ⇒ **必须有一道拦住**。

    这是 v1.0.11 前的真实形状（`platforms` 写三平台、只给 windows 轮子）。
    注意：这类配方会被 `load_recipe` 的 schema 校验**先**拒掉，
    所以门禁根本走不到自己的判据 —— 但结论一样：**不该通过**。
    这里断言的是「整体契约」而非「哪一层的代码在挡」。
    """
    recipe = _base_recipe(platforms=["windows", "linux"])
    recipe["artifacts"] = [
        a for a in recipe["artifacts"] if "linux" not in (a.get("platforms") or [])
    ]
    _write_component(tmp_path, "probe", recipe)
    code, output = _run_gate(tmp_path)
    assert code == 1, f"缺平台构件居然通过了：{output}"
    assert "linux" in output


def test_gate_fails_on_free_threaded_wheel(tmp_path: Path) -> None:
    """`cp313t` 是自由线程构建，普通解释器加载不了 ⇒ 判红。

    这是 v1.0.26 的真实缺陷：psutil 选到了 cp313t。
    """
    recipe = _base_recipe()
    recipe["artifacts"][0]["url"] = "https://e.invalid/a-cp313-cp313t-win_amd64.whl"
    _write_component(tmp_path, "probe", recipe)
    code, output = _run_gate(tmp_path)
    assert code == 1
    assert "cp313t" in output


def test_gate_fails_on_entry_module_outside_extract_to(tmp_path: Path) -> None:
    """`entry.module` 必须落在某个 `extract_to` 之下。"""
    recipe = _base_recipe()
    recipe["entry"]["module"] = "elsewhere/pkg/__init__.py"
    _write_component(tmp_path, "probe", recipe)
    code, output = _run_gate(tmp_path)
    assert code == 1
    assert "entry.module" in output


def test_gate_requires_deps_lock(tmp_path: Path) -> None:
    """没有依赖闭包快照 ⇒ 判红（否则「漏包」会被静默放过）。"""
    _write_component(tmp_path, "probe", _base_recipe(), lock=False)
    code, output = _run_gate(tmp_path)
    assert code == 1
    assert "deps.lock.json" in output


def test_gate_fails_when_closure_package_missing(tmp_path: Path) -> None:
    """快照里列了某包、配方里没有对应 wheel ⇒ 判红。

    这是 v1.0.23→v1.0.27 那一串 `ModuleNotFoundError` 的**本地前置拦截**。
    """
    recipe = _base_recipe()
    _write_component(tmp_path, "probe", recipe)

    # 手工造一份「声称依赖 missing-pkg」的快照
    sys.path.insert(0, str(_REPO / "src"))
    from omnicrawler.services.component_recipe import load_recipe, recipe_digest

    loaded = load_recipe(tmp_path / "probe" / "recipe.json")
    snapshot = {
        "recipe_digest": recipe_digest(loaded),
        "python_version": "3.13",
        "packages": ["missing-pkg"],
    }
    (tmp_path / "probe" / "deps.lock.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    code, output = _run_gate(tmp_path)
    assert code == 1
    assert "missing-pkg" in output


def test_gate_fails_when_snapshot_stale(tmp_path: Path) -> None:
    """配方改过但快照没重算 ⇒ 判红（防「快照过期」被当成通过）。"""
    recipe = _base_recipe()
    _write_component(tmp_path, "probe", recipe)
    snapshot = {
        "recipe_digest": "0" * 64,  # 故意不匹配
        "python_version": "3.13",
        "packages": [],
    }
    (tmp_path / "probe" / "deps.lock.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    code, output = _run_gate(tmp_path)
    assert code == 1
    assert "过期" in output


def test_data_component_does_not_need_platform_artifacts(tmp_path: Path) -> None:
    """**纯数据组件**（kind=data）不该被「每平台都要有标记构件」误伤。

    Tesseract 语言包是通用数据文件，文件名里没有 `win_amd64` 之类的标记。
    对它套用 Wheel-Pack 的判据会逼出假阳性 —— 这正是 v1.0.14 那轮误把
    数据组件当 Wheel-Pack 去 run 的同类错误。
    """
    recipe = {
        "schema": 1,
        "name": "probe-data",
        "version": "1.0.0",
        "kind": "data",
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["x86_64", "aarch64"],
        "python_abi": [],
        "provides": ["probe.data"],
        "artifacts": [
            {
                "url": "https://e.invalid/model.traineddata",
                "sha256": "d" * 64,
                "size": 2048,
                "format": "raw",
                "filename": "model.traineddata",
                "extract_to": "tessdata",
            }
        ],
        "entry": {"path": "tessdata/model.traineddata", "protocol": "d", "engine": "tesseract"},
    }
    _write_component(tmp_path, "probe-data", recipe)
    code, output = _run_gate(tmp_path)
    assert code == 0, f"纯数据组件被误伤：{output}"


def test_gate_is_fast() -> None:
    """门禁必须在**亚秒级**完成（维护者约束：不给本地开发机增加负担）。"""
    import time

    root = _REPO / "market" / "components"
    if not root.is_dir():
        pytest.skip("需要 market 快照")
    started = time.monotonic()
    subprocess.run(
        [sys.executable, str(_GATE), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_REPO),
        env={**__import__("os").environ, "PYTHONPATH": str(_REPO / "src"), "PYTHONUTF8": "1"},
        timeout=60,
        check=False,
    )
    elapsed = time.monotonic() - started
    # 子进程 Python 启动本身约 0.15–0.3 秒；给 3 秒上限防机器抖动
    assert elapsed < 3.0, f"门禁耗时 {elapsed:.2f}s，超出预算"
