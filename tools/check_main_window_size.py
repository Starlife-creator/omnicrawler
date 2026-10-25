#!/usr/bin/env python3
"""C-#19：``MainWindow`` 组合根规模 ratchet 门禁（只降不升）。

背景：``gui/main.py`` 是 GUI 组合根，历史上持续膨胀为上帝对象（基线实测
2533 行 / 110 个类方法 / 最长方法 227 行）。物理拆分受 Qt offscreen 测试网
保护，分阶段迁移见 ``docs/MAINWINDOW_REFACTOR.md``；本门禁先守住「不再膨胀」：

* 文件总行数、``MainWindow`` 方法数、最长方法行数均不得超过基线上限；
* 基线只允许向下校准（拆分后用 ``--write-baseline`` 按实测重写），禁止向上放宽。

用法::

    python tools/check_main_window_size.py
    python tools/check_main_window_size.py --write-baseline   # 拆分后维护者校准
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_FILE = PROJECT_ROOT / "src" / "omnicrawler" / "gui" / "main.py"
MAIN_CLASS = "MainWindow"
DEFAULT_BASELINE = PROJECT_ROOT / "tools" / "main-window-baseline.json"

METRICS = ("file_lines", "method_count", "longest_method_lines")


def measure(main_file: Path = MAIN_FILE) -> dict[str, int]:
    """实测规模指标。"""
    text = main_file.read_text(encoding="utf-8")
    file_lines = len(text.splitlines())
    tree = ast.parse(text, filename=str(main_file))

    method_count = 0
    longest_method_lines = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != MAIN_CLASS:
            continue
        methods = [
            child
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        method_count = len(methods)
        for method in methods:
            span = (method.end_lineno or method.lineno) - method.lineno + 1
            longest_method_lines = max(longest_method_lines, span)

    return {
        "file_lines": file_lines,
        "method_count": method_count,
        "longest_method_lines": longest_method_lines,
    }


def check(main_file: Path = MAIN_FILE, baseline: Path = DEFAULT_BASELINE) -> list[str]:
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    limits: dict[str, int] = payload["limits"]
    actual = measure(main_file)

    issues: list[str] = []
    labels = {
        "file_lines": "文件总行数",
        "method_count": "类方法数",
        "longest_method_lines": "最长方法行数",
    }
    for key in METRICS:
        if actual[key] > limits[key]:
            issues.append(
                f"MainWindow {labels[key]} {actual[key]} 超出基线上限 {limits[key]}"
                "（C-#19 只降不升；拆分计划见 docs/MAINWINDOW_REFACTOR.md）"
            )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MainWindow 规模 ratchet 门禁（C-#19）")
    parser.add_argument(
        "--baseline", type=Path, default=DEFAULT_BASELINE, help="基线 JSON 路径"
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="按当前实测重写基线（仅拆分完成后的维护者校准使用）",
    )
    args = parser.parse_args(argv)

    actual = measure(MAIN_FILE)
    if args.write_baseline:
        payload = {
            "_meta": {
                "purpose": "MainWindow 组合根规模上限：只降不升（C-#19）。",
                "rules": "file_lines=main.py 总行数；method_count=MainWindow 类方法数；"
                "longest_method_lines=最长方法行跨度；拆分迁移见 docs/MAINWINDOW_REFACTOR.md。",
            },
            "file": "src/omnicrawler/gui/main.py",
            "class": MAIN_CLASS,
            "limits": {key: actual[key] for key in METRICS},
        }
        args.baseline.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已写入基线：{args.baseline}（{actual}）")
        return 0

    issues = check(MAIN_FILE, args.baseline)
    if issues:
        print("MainWindow 规模门禁失败：", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["limits"]
    print(
        "MainWindow 规模在基线内："
        + ", ".join(f"{key}={actual[key]}/{baseline[key]}" for key in METRICS)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
