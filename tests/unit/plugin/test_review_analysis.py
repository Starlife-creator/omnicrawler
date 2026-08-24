"""Phase 3 Q4/G3：审核辅助分析契约测试（AI 增强审核员，纯静态证据）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_review import review_analyze

pytestmark = pytest.mark.plugin_contract


@pytest.fixture()
def contract2_with_egress(tmp_path: Path) -> Path:
    """契约 2 插件：读 records + 网络外传（J2 风险信号）+ 危险调用。"""
    plugin = tmp_path / "plugin.py"
    plugin.write_text(
        "PLUGIN_METADATA = {'name': 'x', 'permissions': ['records:read', 'network:scoped']}\n"
        "import omnicrawler_sdk\n"
        "def handle(op, p):\n"
        "    r = omnicrawler_sdk.call('records.read', {})\n"
        "    return omnicrawler_sdk.call('network.fetch', {'url': 'https://x'})\n"
        "def helper():\n"
        "    return eval('1+1')\n",
        encoding="utf-8",
    )
    return plugin


def test_analysis_extracts_capabilities_and_egress(contract2_with_egress: Path) -> None:
    analysis = review_analyze(contract2_with_egress)
    assert analysis.contract_shape == 2
    assert "records.read" in analysis.capabilities_called
    assert "network.fetch" in analysis.capabilities_called
    assert analysis.has_network_fetch is True
    assert "records.read" in analysis.record_ops
    # 数据外传模式：read + fetch 共现（J2 信号）
    assert {"records.read", "network.fetch"} <= set(analysis.capabilities_called)


def test_analysis_marks_dangerous_calls(contract2_with_egress: Path) -> None:
    analysis = review_analyze(contract2_with_egress)
    assert "eval" in analysis.dangerous_calls


def test_analysis_imports(tmp_path: Path) -> None:
    plugin = tmp_path / "p.py"
    plugin.write_text(
        "import json\nfrom pathlib import Path\nimport requests\n"
        "def handle(op, p):\n    return {}\n",
        encoding="utf-8",
    )
    analysis = review_analyze(plugin)
    assert "json" in analysis.imports
    assert "pathlib" in analysis.imports
    assert "requests" in analysis.imports


def test_analysis_metadata_fields_and_contract1(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.py"
    legacy.write_text(
        "PLUGIN_METADATA = {'name': 'old', 'permissions': []}\n"
        "def register(registry):\n    pass\n",
        encoding="utf-8",
    )
    analysis = review_analyze(legacy)
    assert analysis.contract_shape == 1
    assert "name" in analysis.metadata_fields
    assert "permissions" in analysis.metadata_fields
