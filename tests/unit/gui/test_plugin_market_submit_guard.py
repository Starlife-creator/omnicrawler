"""投稿前自查守卫：门 3 缺 dependencies 必须在上传前拦住（市场侧是硬拒）。"""
from __future__ import annotations

from pathlib import Path

from omnicrawler.gui.views.plugin_market_logic import pre_submit_blockers


def _write_plugin(root: Path, metadata: str) -> Path:
    plugin = root / "demo"
    plugin.mkdir()
    (plugin / "plugin.py").write_text(
        f"PLUGIN_METADATA={metadata}\n"
        "def handle(operation, payload):\n    return {}\n",
        encoding="utf-8",
    )
    return plugin


def test_missing_dependencies_blocks_submission(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'demo','version':'1.0.0','description':'d','license':'MIT',"
        "'plugin_types':['source'],'permissions':[],'execution_mode':'subprocess'}",
    )
    blockers = pre_submit_blockers(plugin)
    assert any("gate3_dependencies_missing" in item for item in blockers), blockers
    assert any("市场投稿会直接拒收" in item for item in blockers), blockers


def test_valid_zero_dependency_plugin_is_submittable(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'demo','version':'1.0.0','description':'d','license':'MIT',"
        "'plugin_types':['source'],'permissions':[],'execution_mode':'subprocess',"
        "'dependencies':[]}",
    )
    assert pre_submit_blockers(plugin) == []
