#!/usr/bin/env python3
"""C-#30：GUI 测试双目录归属规则门禁与重复覆盖清单。

边界口径（详见 ``docs/CODING_STANDARDS.md``「GUI 测试归属」节）：

- ``tests/unit/gui``：纯逻辑、组件单元、信号/参数契约；不依赖完整 QApplication
  生命周期，Qt 不可用时应可 ``pytest.importorskip("PySide6")`` 跳过。
- ``tests/gui``：窗口/对话框生命周期、集成、跨组件契约、offscreen 端到端冒烟；
  集中使用 ``tests/gui/conftest.py`` 的 offscreen 环境。

阻断规则（新增违规即失败）：

1. 两个目录不得出现同名测试文件（避免归属混淆与重复用例）；
2. ``tests/unit/gui`` 不得 import ``tests/gui``（单元层不得依赖集成层 fixture/helper）；
3. ``tests/gui`` 可复用 ``tests/unit/gui`` 的 helper，反向禁止。

``--report`` 额外输出「同一 gui 源模块被两个目录同时覆盖」的重复候选清单
（仅 advisory，供人工去重；物理去重受 Qt 测试环境约束，见回传报告 C-#30 状态）。

用法::

    python tools/check_gui_test_layout.py
    python tools/check_gui_test_layout.py --report
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UNIT_GUI = PROJECT_ROOT / "tests" / "unit" / "gui"
GUI = PROJECT_ROOT / "tests" / "gui"

#: 测试文件中引用的 omnicrawler.gui 源模块路径。
_GUI_MODULE_RE = re.compile(
    r"from\s+omnicrawler\.gui\.([A-Za-z0-9_.]+)\s+import|import\s+omnicrawler\.gui\.([A-Za-z0-9_.]+)"
)
#: 跨目录 import 探测（tests.unit.gui ↔ tests.gui）。
_CROSS_UNIT_TO_GUI = re.compile(r"from\s+tests\.gui\b|import\s+tests\.gui\b")
_CROSS_GUI_TO_UNIT = re.compile(r"from\s+tests\.unit\.gui\b|import\s+tests\.unit\.gui\b")


def _test_files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {path.name: path for path in sorted(root.rglob("test_*.py"))}


def _imported_gui_modules(text: str) -> set[str]:
    modules: set[str] = set()
    for match in _GUI_MODULE_RE.finditer(text):
        module = match.group(1) or match.group(2)
        if module:
            modules.add(module.rstrip("."))
    return modules


def check(root: Path = PROJECT_ROOT) -> list[str]:
    """返回阻断问题清单（空列表即通过）。"""
    issues: list[str] = []
    unit_files = _test_files(root / "tests" / "unit" / "gui")
    gui_files = _test_files(root / "tests" / "gui")

    # 规则 1：同名测试文件不得跨目录重复。
    for name in sorted(set(unit_files) & set(gui_files)):
        issues.append(
            f"测试文件同名跨目录重复: tests/unit/gui/{name} 与 tests/gui/{name}；"
            "按归属规则保留一处，另一处迁移或合并（C-#30）"
        )

    # 规则 2 / 3：跨目录 import 方向约束。
    for name, path in unit_files.items():
        text = path.read_text(encoding="utf-8")
        if _CROSS_UNIT_TO_GUI.search(text):
            issues.append(
                f"tests/unit/gui/{name} 不得 import tests/gui（单元层禁止依赖集成层，C-#30）"
            )
    for name, path in gui_files.items():
        text = path.read_text(encoding="utf-8")
        if _CROSS_GUI_TO_UNIT.search(text):
            issues.append(
                f"tests/gui/{name} 不得 import tests/unit/gui（集成层可复用 helper，"
                "但反向依赖会让单元层被集成夹具绑架，C-#30）"
            )

    return issues


def overlap_report(root: Path = PROJECT_ROOT) -> list[tuple[str, list[str], list[str]]]:
    """advisory：返回 ``(gui 模块, unit/gui 测试文件, tests/gui 测试文件)`` 重复候选。"""
    unit_files = _test_files(root / "tests" / "unit" / "gui")
    gui_files = _test_files(root / "tests" / "gui")

    covered: dict[str, dict[str, set[str]]] = {}
    for directory, files in (("unit", unit_files), ("gui", gui_files)):
        for name, path in files.items():
            for module in _imported_gui_modules(path.read_text(encoding="utf-8")):
                covered.setdefault(module, {"unit": set(), "gui": set()})[directory].add(name)

    overlap: list[tuple[str, list[str], list[str]]] = []
    for module in sorted(covered):
        unit_refs = sorted(covered[module]["unit"])
        gui_refs = sorted(covered[module]["gui"])
        if unit_refs and gui_refs:
            overlap.append((module, unit_refs, gui_refs))
    return overlap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GUI 测试双目录归属规则门禁（C-#30）")
    parser.add_argument(
        "--report",
        action="store_true",
        help="额外输出同模块双目录覆盖的重复候选清单（advisory，不影响退出码）",
    )
    args = parser.parse_args(argv)

    issues = check(PROJECT_ROOT)
    if args.report:
        print("== GUI 测试重复覆盖候选（advisory，人工去重） ==")
        overlap = overlap_report(PROJECT_ROOT)
        if not overlap:
            print("（无同一模块被双目录同时覆盖的候选）")
        for module, unit_refs, gui_refs in overlap:
            print(f"- {module}")
            print(f"    tests/unit/gui: {', '.join(unit_refs)}")
            print(f"    tests/gui:      {', '.join(gui_refs)}")

    if issues:
        print("GUI 测试归属检查失败：", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("GUI 测试归属检查通过（无双名文件、无反向跨目录依赖）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
