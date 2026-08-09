"""CLI wrapper for runtime manifest creation.

F9: build_windows.ps1 通过参数传递路径，不再把 PowerShell 变量插值进
Python 源码字符串（避免路径含单引号/反斜杠时的语法错与注入）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from omnicrawl.core.runtime_manifest import create_runtime_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the portable runtime integrity manifest")
    parser.add_argument("--release-root", type=Path, required=True)
    args = parser.parse_args()
    create_runtime_manifest(Path(args.release_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
