"""`tools/qa/check_test_cleanliness.py` 的「被忽略残留」判据。

为什么要有这个文件：该守卫原先只看 `git status`，于是「测试往仓库里写了东西、
而 `.gitignore` 恰好忽略了那个形态」这一整类污染**完全不可见**。
实测（2026-11-24）仓库里躺着 65 个 `src/omnicrawler/plugins/omnicrawler-landlock-*/`
与 37 个根目录 `tmp*/` 空目录，`git status` 一个都没报，守卫却报「工作区干净」。

本文件钉三件事：

* 形态匹配能抓住**没见过的名字**（不是白名单）；
* 无关的被忽略条目（`.venv/`、`.pytest_cache/`）不许误报；
* `git status` 干净、只剩被忽略残留时**仍然判红**（这正是原守卫的缺口）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "qa" / "check_test_cleanliness.py"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_cleanliness_tool", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "entry",
    [
        "src/omnicrawler/plugins/omnicrawler-landlock-1rjkfpb6",
        "src/omnicrawler/plugins/omnicrawler-landlock-never-seen-before",
        "tmp087u0q74",
        "tmps1eiti01",
    ],
)
def test_ignored_leak_shapes_are_caught_without_a_whitelist(monkeypatch: pytest.MonkeyPatch, entry: str) -> None:
    """形态匹配必须抓住**没见过的名字**：白名单会漏掉下一次的新目录。"""
    tool = _load_tool()
    monkeypatch.setattr(tool, "_git_ignored_entries", lambda: [entry])
    assert tool.ignored_leaks() == [entry]


@pytest.mark.parametrize(
    "entry",
    [
        ".venv/",
        ".pytest_cache/",
        ".runtime/",
        "build_cache/",
        "cov_html/",
        "src/omnicrawler/plugins/template_note",
        "artifacts/",
    ],
)
def test_unrelated_ignored_entries_are_not_reported(monkeypatch: pytest.MonkeyPatch, entry: str) -> None:
    """误报会让判据失效：被忽略的正常产物（缓存、venv）不是污染。"""
    tool = _load_tool()
    monkeypatch.setattr(tool, "_git_ignored_entries", lambda: [entry])
    assert tool.ignored_leaks() == [], f"{entry} 被误判为残留"


def test_clean_tree_passes_both_checks(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """工作区干净且无残留时必须绿 —— 判据不能只会红。"""
    tool = _load_tool()
    monkeypatch.setattr(tool, "_git_status", lambda: [])
    monkeypatch.setattr(tool, "_git_ignored_entries", lambda: [])
    assert tool.main([]) == 0
    assert "工作区干净" in capsys.readouterr().out


def test_leaks_alone_fail_even_when_git_status_is_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """这正是原守卫的缺口：git status 干净、但仓库里有被忽略的残留。"""
    tool = _load_tool()
    monkeypatch.setattr(tool, "_git_status", lambda: [])
    monkeypatch.setattr(
        tool,
        "_git_ignored_entries",
        lambda: ["tmpdeadbeef", "src/omnicrawler/plugins/omnicrawler-landlock-x"],
    )
    assert tool.main([]) == 1, "有残留时不得报绿"
    out = capsys.readouterr().out
    assert "被忽略的残留" in out
    assert "src/omnicrawler/plugins/omnicrawler-landlock-x" in out
