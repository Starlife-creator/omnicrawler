#!/usr/bin/env python3
"""批量刷新现行文档头部「适用版本」标记（C-#5）。

版本真源是 ``pyproject.toml`` 的 ``project.version``；本脚本把现行文档头部
``> 适用版本：<旧版本>`` 统一对齐到当前版本，保留行内粗体标记与其余说明文字。

不纳入刷新的文档（历史记录 / 特殊账本，版本漂移不计入现行口径）：

* ``docs/LOCAL_OPTIMIZATION_2026-10-02.md`` —— 归档入口；
* ``docs/OPTIMIZATION_BASELINE_2026-10-03.md`` —— 归档入口；
* ``docs/OPTIMIZATION_EXECUTION_2026-10-03.md`` —— 归档入口；
* ``docs/TASK_ACCEPTANCE_LEDGER.md`` —— 验收账本，每行是独立历史声明，
  头部版本靠工作流证据重跑更新，不靠改字升级。

用法::

    python tools/refresh_doc_versions.py            # 批量刷新并报告改动文件
    python tools/refresh_doc_versions.py --check   # 门禁模式：不一致即退出 1，不写盘
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 历史归档入口与特殊账本：头部版本保持原样（见模块 docstring 说明）。
EXCLUDED_DOCS = {
    "docs/LOCAL_OPTIMIZATION_2026-10-02.md",
    "docs/OPTIMIZATION_BASELINE_2026-10-03.md",
    "docs/OPTIMIZATION_EXECUTION_2026-10-03.md",
    "docs/TASK_ACCEPTANCE_LEDGER.md",
}

#: 仓库根目录下同样携带「适用版本」标记的现行文档。
ROOT_DOCS = ("AGENTS.md",)

_MARKER = re.compile(r"(适用版本：)(\*\*)?[0-9]+\.[0-9]+\.[0-9]+(\*\*)?")


def current_version(root: Path = PROJECT_ROOT) -> str:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(metadata["project"]["version"])


def _target_docs(root: Path) -> list[Path]:
    docs_dir = root / "docs"
    targets = [path for path in sorted(docs_dir.glob("*.md"))]
    targets += [root / name for name in ROOT_DOCS if (root / name).is_file()]
    return targets


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _refresh_line(line: str, version: str) -> str:
    def repl(match: re.Match[str]) -> str:
        bold_open = match.group(2) or ""
        bold_close = match.group(3) or ""
        return f"{match.group(1)}{bold_open}{version}{bold_close}"

    return _MARKER.sub(repl, line)


def refresh(root: Path = PROJECT_ROOT, *, apply: bool = True) -> tuple[list[str], list[str]]:
    """刷新现行文档版本头。返回 ``(改动文件清单, 问题清单)``。

    ``apply=False``（门禁模式）只比对不写盘，问题清单列出版本不一致的文档。
    """
    version = current_version(root)
    changed: list[str] = []
    drift: list[str] = []

    for path in _target_docs(root):
        rel = _rel(root, path)
        if rel in EXCLUDED_DOCS:
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        refreshed: list[str] = []
        touched = False
        marker_found = False
        for line in lines:
            if "适用版本：" in line:
                marker_found = True
                new_line = _refresh_line(line, version)
                if new_line != line:
                    touched = True
                refreshed.append(new_line)
            else:
                refreshed.append(line)
        if not marker_found:
            continue
        if touched:
            drift.append(f"{rel}: 版本标记未对齐 {version}")
            if apply:
                path.write_text("".join(refreshed), encoding="utf-8")
                changed.append(rel)
    # 写盘模式下漂移已就地修复，不再回传问题清单；门禁模式只比对不写盘。
    issues = [] if apply else drift
    return changed, issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新现行文档头部适用版本标记")
    parser.add_argument(
        "--check",
        action="store_true",
        help="门禁模式：只比对不写盘，发现不一致退出 1",
    )
    args = parser.parse_args(argv)

    changed, issues = refresh(PROJECT_ROOT, apply=not args.check)
    if args.check:
        if issues:
            print("文档版本标记与应用版本不一致：", file=sys.stderr)
            for issue in issues:
                print(f"- {issue}", file=sys.stderr)
            print("请运行 `python tools/refresh_doc_versions.py` 批量刷新。", file=sys.stderr)
            return 1
        print("文档版本标记与应用版本一致。")
        return 0

    version = current_version()
    if changed:
        print(f"已把 {len(changed)} 份文档版本头刷新到 {version}：")
        for rel in changed:
            print(f"- {rel}")
    else:
        print(f"所有现行文档版本头已是 {version}，无需改动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
