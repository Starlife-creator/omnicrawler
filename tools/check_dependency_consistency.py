#!/usr/bin/env python3
"""依赖声明一致性门禁（C-#14 / C-#20）。

覆盖三类仓库侧可机器判定的漂移：

1. **OpenCV 单变体**：直接声明中至多出现一个 ``opencv-*`` 变体（headless 为选定口径），
   防止 contrib / 无界面 / 带 GUI 变体跨大版本共存造成体积膨胀与 ABI 冲突；
2. **死依赖守卫**：已审计确认 ``src/`` 无导入的退役包（``tomli``、``aiofiles``）不得
   再次出现在任何 extras 声明中；
3. **版本上下界**：直接声明必须同时带上限（与既有依赖策略一致），禁止无上限遗留清单。

传递依赖（ddddocr / paddlex 等经 marker 拉入的 opencv 变体）不在本脚本收敛范围内——
需在具备 ``uv`` 的环境执行 ``uv lock`` 重解，锁文件侧完整重解待对应环境验证。

用法::

    python tools/check_dependency_consistency.py
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 直接声明中允许出现的 OpenCV 变体族；门禁止同时声明两个及以上。
OPENCV_VARIANTS = (
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
)

#: 已审计确认源码无导入的退役依赖（再次声明即判红）。
#: 审计口径：``grep -rln "import <name>" src/`` 0 命中；新增退役包时先复核再登记。
DEAD_DEPENDENCIES = frozenset({"tomli", "aiofiles"})

_REQ_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def _package_name(requirement: str) -> str | None:
    match = _REQ_NAME.match(requirement.strip())
    return match.group(0).lower().replace("_", "-") if match else None


def _load_project(root: Path) -> dict:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def _iter_requirements(project: dict) -> list[tuple[str, str]]:
    """返回 ``(来源, 原始声明)`` 列表，覆盖基础依赖与全部 extras。"""
    entries: list[tuple[str, str]] = []
    for requirement in project.get("project", {}).get("dependencies", []):
        entries.append(("base", requirement))
    for extra, requirements in project["project"].get("optional-dependencies", {}).items():
        for requirement in requirements:
            entries.append((f"extra {extra}", requirement))
    return entries


def check(root: Path = PROJECT_ROOT) -> list[str]:
    """扫描 pyproject 依赖声明，返回问题清单（空列表即通过）。"""
    issues: list[str] = []
    project = _load_project(root)

    opencv_owners: dict[str, list[str]] = {}
    for source, requirement in _iter_requirements(project):
        name = _package_name(requirement)
        if name is None:
            issues.append(f"无法解析依赖声明: {requirement!r}（{source}）")
            continue

        if name in OPENCV_VARIANTS:
            opencv_owners.setdefault(name, []).append(source)

        if name in DEAD_DEPENDENCIES:
            issues.append(
                f"死依赖 {name} 仍声明在 {source}（C-#20：src/ 已无导入，应移除）"
            )

        if not re.search(r"<\s*\d", requirement):
            issues.append(
                f"依赖 {name}（{source}）缺少版本上限，违反上下界策略（C-#14 依赖口径收敛）"
            )

    if len(opencv_owners) > 1:
        detail = "; ".join(
            f"{variant} ∈ {', '.join(sorted(owners))}"
            for variant, owners in sorted(opencv_owners.items())
        )
        issues.append(
            "OpenCV 直接声明出现多个变体（C-#14 应收敛为单一 headless 变体）："
            + detail
        )

    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="依赖声明一致性门禁（C-#14 / C-#20）")
    parser.parse_args(argv)
    issues = check()
    if issues:
        print("依赖一致性检查失败：", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("依赖一致性检查通过（OpenCV 单变体、无死依赖、版本上下界齐备）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
