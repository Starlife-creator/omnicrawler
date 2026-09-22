"""更新时权限变更 diff 的自测 —— §4.6：更新扩大权限必须重新呈现并处理授权。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.gui.views.plugin_market_logic import (
    _installed_permissions,
    _permission_diff,
    _permission_diff_text,
)


def test_diff_reports_added_and_removed() -> None:
    diff = _permission_diff(["files:read", "network:scoped"], {"permissions": ["files:read", "records:write"]})
    assert diff["added"] == ["records:write"]
    assert diff["removed"] == ["network:scoped"]


def test_no_change_means_empty_diff() -> None:
    diff = _permission_diff(["files:read"], {"permissions": ["files:read"]})
    assert diff == {"added": [], "removed": []}


def test_comparison_is_case_insensitive() -> None:
    """权限名按 casefold 归一：仅大小写变化不算新增（展示保留原写法）。"""
    diff = _permission_diff(["Files:Read"], {"permissions": ["files:read"]})
    assert diff["added"] == []


def test_widened_means_added_permissions_only() -> None:
    """「扩权」判据＝新增权限；收窄（只删不加）不算扩权。"""
    narrowed = _permission_diff(["files:read", "network:scoped"], {"permissions": ["files:read"]})
    assert narrowed["added"] == []
    widened = _permission_diff([], {"permissions": ["secrets:read"]})
    assert widened["added"] == ["secrets:read"]


def test_diff_text_names_the_change(tmp_path: Path) -> None:
    text = _permission_diff_text({"added": ["secrets:read"], "removed": []})
    assert "新增权限" in text and "secrets:read" in text


def test_installed_permissions_read_statically(tmp_path: Path) -> None:
    """从已安装插件目录 AST 静态读权限（不执行代码）。"""
    (tmp_path / "plugin.py").write_text(
        "PLUGIN_METADATA = {\n    'name': 'x',\n    'permissions': ['files:read', 'temp:write'],\n}\n",
        encoding="utf-8",
    )
    assert _installed_permissions(tmp_path) == ["files:read", "temp:write"]


def test_installed_permissions_empty_when_unreadable(tmp_path: Path) -> None:
    assert _installed_permissions(tmp_path) == []
    (tmp_path / "plugin.py").write_text("def broken(:\n", encoding="utf-8")
    assert _installed_permissions(tmp_path) == []
