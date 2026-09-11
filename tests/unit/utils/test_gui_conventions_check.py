"""Contract tests for the GUI conventions gate (design-system inheritance).

门禁本身必须有测试：否则规则写错、baseline 被手改放水都不会被发现，
门禁会静默失效——这比没有门禁更危险。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_checker() -> ModuleType:
    path = REPO_ROOT / "tools" / "check_gui_conventions.py"
    spec = importlib.util.spec_from_file_location("check_gui_conventions", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, name: str, body: str) -> Path:
    target = tmp_path / name
    target.write_text(body, encoding="utf-8")
    return target


def test_token_whitelist_covers_surfaces_and_white(tmp_path: Path) -> None:
    """令牌真源取自 design_system.py，且必须放行纯白（设计系统显式豁免）。"""
    checker = _load_checker()
    whitelist = checker._token_hex_whitelist()
    assert "#FFFFFF" in whitelist
    assert len(whitelist) > 10, "应从 design_system 解析出全部令牌色值"


def test_detects_missing_a11y_inline_style_and_raw_hex(tmp_path: Path) -> None:
    """三类违规都要能抓到：缺无障碍名 / 内联样式 / 裸十六进制。"""
    checker = _load_checker()
    source = _write(
        tmp_path,
        "probe.py",
        "from PySide6.QtWidgets import QWidget\n\n\n"
        "class Probe(QWidget):\n"
        "    def __init__(self) -> None:\n"
        "        super().__init__()\n"
        '        self.setStyleSheet("color: #123456;")\n',
    )
    counts = checker.scan_file(source, checker._token_hex_whitelist())
    assert counts["a11y"] == 1, counts
    assert counts["inline"] == 1, counts
    assert counts["raw_hex"] == 1, counts


def test_accessible_name_and_token_color_pass(tmp_path: Path) -> None:
    """合规写法（设无障碍名 + 用令牌 + 无内联样式）不应被误报。"""
    checker = _load_checker()
    whitelist = checker._token_hex_whitelist()
    token = sorted(whitelist)[0]
    source = _write(
        tmp_path,
        "ok.py",
        "from PySide6.QtWidgets import QWidget\n\n\n"
        "class Ok(QWidget):\n"
        "    def __init__(self) -> None:\n"
        "        super().__init__()\n"
        '        self.setAccessibleName("示例")\n'
        f'        self._color = "{token}"\n',
    )
    counts = checker.scan_file(source, whitelist)
    assert counts == {"a11y": 0, "inline": 0, "raw_hex": 0}, counts


def test_baseline_is_zero_slack_and_lists_existing_offenders() -> None:
    """baseline 必须与实测一致（零余量），且只记录真实存在的违规。"""
    checker = _load_checker()
    payload = json.loads((REPO_ROOT / "tools" / "gui-conventions-baseline.json").read_text("utf-8"))
    baseline = payload.get("files", {})
    assert baseline, "baseline 不应为空——存量违规需要被如实登记"
    actual = checker.collect(checker._token_hex_whitelist())
    for rel, counts in baseline.items():
        current = actual.get(rel)
        assert current is not None, f"{rel} 已无违规，应从 baseline 移除"
        for key, allowed in counts.items():
            assert current.get(key, 0) == int(allowed), f"{rel}.{key} 与 baseline 不一致（{current.get(key, 0)} vs {allowed}）"
    for rel in actual:
        assert rel in baseline, f"{rel} 出现违规但不在 baseline（新文件应零容忍）"
