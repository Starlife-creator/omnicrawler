"""Phase 3：plugins scaffold-contract2 脚手架契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_inspector import inspect_plugin
from omnicrawler.plugins.plugin_sdk import scaffold_contract2
from omnicrawler.plugins.plugin_sdk_contract2 import validate_contract2_id

pytestmark = pytest.mark.plugin_contract


def test_scaffold_creates_contract2_layout(tmp_path: Path) -> None:
    root = scaffold_contract2(tmp_path, "scaff_demo", display_name="脚手架演示")
    assert (root / "plugin.py").is_file()
    assert (root / "plugin.yaml").is_file()
    assert (root / "listing.md").is_file()
    assert (root / "tests" / "test_contract.py").is_file()


def test_scaffold_plugin_inspector_detects_contract2(tmp_path: Path) -> None:
    root = scaffold_contract2(tmp_path, "scaff_demo")
    inspection = inspect_plugin(root / "plugin.py")
    assert inspection.contract_shape == 2
    assert inspection.execution_mode == "subprocess"
    assert inspection.compatible, inspection.errors


def test_scaffold_handle_returns_seed_requests(tmp_path: Path) -> None:
    """脚手架 handle 的 source.seed 操作可运行（协议不变式）。"""
    root = scaffold_contract2(tmp_path, "scaff_demo")
    namespace: dict = {}
    exec((root / "plugin.py").read_text(encoding="utf-8"), namespace)  # noqa: S102
    result = namespace["handle"]("source.seed", {})
    assert result == {"requests": [{"url": "https://example.com/"}]}
    # 未知操作返回 dict（协议不变式）
    assert isinstance(namespace["handle"]("weird.op", {}), dict)


def test_scaffold_id_validation() -> None:
    validate_contract2_id("ok_plugin2")  # 合法
    with pytest.raises(ValueError):
        validate_contract2_id("UpperCamel")
    with pytest.raises(ValueError):
        validate_contract2_id("has space")
    with pytest.raises(ValueError):
        validate_contract2_id("1starts_with_digit")


def test_scaffold_rejects_nonempty_dir(tmp_path: Path) -> None:
    (tmp_path / "scaff_demo").mkdir()
    (tmp_path / "scaff_demo" / "existing.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        scaffold_contract2(tmp_path, "scaff_demo")
