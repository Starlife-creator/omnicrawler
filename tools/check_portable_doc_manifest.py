#!/usr/bin/env python3
"""便携包文档清单校验（C-#15）。

历史问题：三个平台构建脚本曾把 ``docs/`` 整目录拷进便携包，其中
``docs/archive/``（历史评测、研究记录、旧版兼容说明）与 ``docs/releases/``
（历史发布报告）属于追溯材料，既增大包体，又可能让终端用户把旧版口径当成
现行行为。本校验在构建暂存目录与归档冒烟两个时点兜底：

1. ``docs/archive/`` 与 ``docs/releases/`` **不得**出现在便携包内；
2. 必备根文件（README / LICENSE / 第三方声明 / EDITION.txt）必须存在；
3. ``docs/`` 内必须至少有一份现行 Markdown（防止文档被整体漏拷而静默空目录）。

用法::

    python tools/check_portable_doc_manifest.py --release-root <暂存目录>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 禁止随便携包分发的历史文档子目录（相对 docs/）。
FORBIDDEN_DOC_DIRS = ("archive", "releases")

#: 便携包根目录必备文件。
REQUIRED_ROOT_FILES = ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "EDITION.txt")


def check_doc_manifest(release_root: Path) -> list[str]:
    """对便携包暂存目录做清单校验，返回问题清单（空列表即通过）。"""
    issues: list[str] = []

    if not release_root.is_dir():
        return [f"便携包暂存目录不存在: {release_root}"]

    docs_dir = release_root / "docs"
    for dirname in FORBIDDEN_DOC_DIRS:
        forbidden = docs_dir / dirname
        if forbidden.exists():
            issues.append(f"便携包违规携带历史文档目录: docs/{dirname}/（C-#15 要求排除）")

    for name in REQUIRED_ROOT_FILES:
        if not (release_root / name).is_file():
            issues.append(f"便携包缺少必备根文件: {name}")

    if docs_dir.is_dir() and not list(docs_dir.glob("*.md")):
        issues.append("docs/ 内没有现行 Markdown 文档，文档可能被整体漏拷")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="便携包文档清单校验（C-#15）")
    parser.add_argument(
        "--release-root",
        type=Path,
        default=PROJECT_ROOT / "release" / "OmniCrawler",
        help="便携包暂存目录（默认 release/OmniCrawler）",
    )
    args = parser.parse_args(argv)

    issues = check_doc_manifest(args.release_root)
    if issues:
        print("便携包文档清单校验失败：", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("便携包文档清单校验通过（无历史文档目录，必备文件齐备）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
