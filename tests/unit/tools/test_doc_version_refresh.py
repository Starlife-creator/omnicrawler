"""`tools/refresh_doc_versions.py` 版本头批量刷新与门禁反向断言（C-#5）。

反向断言：现行文档版本头必须被刷新到 ``pyproject.toml`` 版本；归档入口与
验收账本保持原样；``--check`` 模式只比对不写盘，存在漂移时退出 1。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_refresh_doc_versions_under_test", _ROOT / "tools" / "refresh_doc_versions.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REFRESH = _load()


def _skeleton(tmp_path: Path, version: str = "2.3.4") -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "{version}"\n', encoding="utf-8"
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "current.md").write_text(
        "> 适用版本：0.15.0 · 配置协议：v5 · 维护状态：现行\n\n正文。\n", encoding="utf-8"
    )
    (docs / "LOCAL_OPTIMIZATION_2026-10-02.md").write_text(
        "> 适用版本：0.15.0 · 归档入口\n", encoding="utf-8"
    )
    (docs / "TASK_ACCEPTANCE_LEDGER.md").write_text(
        "> 适用版本：0.15.0 · 历史账本\n", encoding="utf-8"
    )
    (docs / "OPTIMIZATION_BASELINE_2026-10-03.md").write_text(
        "> 适用版本：0.15.0 · 归档入口\n", encoding="utf-8"
    )
    (docs / "OPTIMIZATION_EXECUTION_2026-10-03.md").write_text(
        "> 适用版本：0.15.0 · 归档入口\n", encoding="utf-8"
    )
    (tmp_path / "AGENTS.md").write_text(
        "> 适用版本：**0.15.0** · 配置协议：v5\n", encoding="utf-8"
    )


def test_refresh_writes_current_docs_and_agents_but_keeps_excluded(tmp_path: Path) -> None:
    _skeleton(tmp_path)

    changed, issues = REFRESH.refresh(tmp_path, apply=True)

    assert "docs/current.md" in changed
    assert "AGENTS.md" in changed
    assert issues == []

    current = (tmp_path / "docs" / "current.md").read_text(encoding="utf-8")
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "2.3.4" in current and "0.15.0" not in current
    assert "2.3.4" in agents and "0.15.0" not in agents

    # 归档入口与账本保持历史版本，不参与升级。
    for excluded in REFRESH.EXCLUDED_DOCS:
        body = (tmp_path / excluded).read_text(encoding="utf-8")
        assert "0.15.0" in body, excluded


def test_check_mode_detects_drift_without_writing(tmp_path: Path) -> None:
    _skeleton(tmp_path)

    changed, issues = REFRESH.refresh(tmp_path, apply=False)

    assert changed == []
    assert any("docs/current.md" in issue for issue in issues)
    assert any("AGENTS.md" in issue for issue in issues)
    # 未写盘：文件仍保留旧版本号。
    assert "0.15.0" in (tmp_path / "docs" / "current.md").read_text(encoding="utf-8")


def test_aligned_repo_has_no_issues(tmp_path: Path) -> None:
    _skeleton(tmp_path, version="2.3.4")
    REFRESH.refresh(tmp_path, apply=True)

    changed, issues = REFRESH.refresh(tmp_path, apply=False)
    assert changed == []
    assert issues == []


def test_check_cli_passes_on_real_repo() -> None:
    """真实仓库当前版本头已对齐（本提交已批量刷新）。"""
    REFRESH.PROJECT_ROOT = _ROOT
    assert REFRESH.main(["--check"]) == 0
