"""`tools/build_wheel.py`：构建 wheel 前必须清掉 `build/`（W6.7-⑥ 的机器验收）。

## 背景（§5.7 登记）

`pip wheel .` 在同一工作树里**第二次**会失败：setuptools 复用 `build/`，里面已有上次的
`.dist-info` ⇒ `[WinError 183] 当文件已存在时，无法创建该文件`。CI 每次干净检出不受影响，
受影响的是**人**按文档照做第二次。

收进脚本后这件事变成可机检的：① 存在 `build/` 时必须被清掉；② 命令形状正确
（`--no-deps`、输出目录）；③ 额外参数原样透传（文档里的 `--no-build-isolation` 之类仍可用）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _module():
    path = _REPO_ROOT / "tools" / "build_wheel.py"
    spec = importlib.util.spec_from_file_location("build_wheel", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_wheel"] = module
    spec.loader.exec_module(module)
    return module


def test_clean_build_dirs_removes_stale_build(tmp_path: Path) -> None:
    """核心：存在 `build/` 时必须被删掉（这正是二次构建失败的原因）。"""
    module = _module()
    (tmp_path / "build" / "lib").mkdir(parents=True)
    (tmp_path / "build" / "lib" / "x.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()

    removed = module.clean_build_dirs(tmp_path)

    assert removed == ["build"], removed
    assert not (tmp_path / "build").exists()
    assert (tmp_path / "src").is_dir(), "清理不得误删其它目录"


def test_clean_build_dirs_is_idempotent(tmp_path: Path) -> None:
    """没有残留时不得报错（脚本要被反复调用）。"""
    module = _module()
    assert module.clean_build_dirs(tmp_path) == []


def test_main_invokes_pip_wheel_with_expected_arguments(tmp_path: Path, monkeypatch) -> None:
    """命令形状：`pip wheel . --no-deps -w <out>` + 额外参数透传；且先清理。"""
    module = _module()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "build").mkdir()

    calls: list[tuple[list[str], str]] = []

    def fake_call(command: list[str], cwd: str) -> int:  # noqa: ANN001
        assert not (tmp_path / "build").exists(), "必须先清理再构建"
        calls.append((list(command), cwd))
        return 0

    monkeypatch.setattr(module.subprocess, "call", fake_call)
    code = module.main(["--root", str(tmp_path), "-w", "out", "--no-build-isolation"])

    assert code == 0
    assert len(calls) == 1
    command, cwd = calls[0]
    assert command[:5] == [sys.executable, "-m", "pip", "wheel", "."]
    assert "--no-deps" in command
    assert "-w" in command and str(tmp_path / "out") in command
    assert command[-1] == "--no-build-isolation", "额外参数应原样透传"
    assert cwd == str(tmp_path)
    assert (tmp_path / "out").is_dir(), "输出目录应被创建"


def test_main_rejects_a_directory_without_pyproject(tmp_path: Path) -> None:
    module = _module()
    assert module.main(["--root", str(tmp_path)]) == 2


def test_ci_uses_the_tool_instead_of_a_raw_pip_wheel() -> None:
    """接线守卫：CI 里不该再有裸 `pip wheel`（否则"先清 build/"又变成靠人记得）。"""
    workflow = (_REPO_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    assert "python -m pip wheel . --no-deps -w dist" not in workflow, (
        "quality.yml 里还有裸 pip wheel —— 请改用 `python tools/build_wheel.py -w dist`"
    )
    assert "tools/build_wheel.py" in workflow, "quality.yml 未接入 tools/build_wheel.py"


@pytest.mark.parametrize("doc", ["docs/WINDOWS_PACKAGING.md"])
def test_packaging_doc_points_at_the_tool(doc: str) -> None:
    """文档也要指向脚本 —— 否则读者照旧手抄命令、第二次构建再炸一次。"""
    text = (_REPO_ROOT / doc).read_text(encoding="utf-8")
    assert "build_wheel.py" in text, f"{doc} 未提到 tools/build_wheel.py"
