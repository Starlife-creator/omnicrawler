#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""维护者工具共用件：三平台运行时制备脚本的公共逻辑。

为什么独立成模块：`tools/prepare_windows_runtime.ps1`、`packaging/prepare_linux_runtime.sh`
与 `packaging/prepare_macos_runtime.sh` 都要在末尾写一份 `runtime-manifest.json`（运行时目录的
构建期证据：每个文件的相对路径 / 字节数 / SHA-256，外加 chrome 路径与总计）。这段逻辑原先三处
各写一遍（PowerShell 一份、两个 bash heredoc 一份），**已经漂移出两个真缺口**：

1. 三处都遍历运行时目录下的**全部**文件，没有排除清单自身。首次制备时该文件还不存在，所以 CI
   一直是绿的；但**重跑**制备脚本时旧清单仍在目录里 ⇒ 清单把自己的哈希也记进去，每次重跑内容都
   不同，无法作为可复现证据。
2. 两个 bash 版用 `p.read_bytes()` 把整个文件读进内存再算 SHA-256。运行时目录里有 Chromium 与
   Paddle 模型，动辄上 GB ⇒ 峰值内存吃满构建机。

所以公共件不只做"去重"，它把这两条也一并修掉：清单自身被跳过，哈希按 1 MiB 分块流式读。
其余制备逻辑（Windows 的哈希钉与 7z 解包、Linux 的 apt 递归依赖与 patchelf、macOS 的 otool
重写与 ad-hoc 重签）平台差异过大，**刻意不抽**——强行统一只会把三种平台的判据揉成一份谁都读不懂
的代码。

用法（三个脚本都按**路径**调用，不依赖 `tools/` 是包）：

    python tools/build_common.py manifest --runtime-root <dir> --chrome <path>
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

#: 清单文件名（小写）。与产品级完整性清单 `RUNTIME-MANIFEST.json`（`core/runtime_manifest.py`）
#: **不是同一个东西**：那个是发给用户的包级校验清单（format/created_at/files 映射），
#: 这个是构建期证据（schema/generated_at/chrome/文件列表）。两者大小写与形状都不同，不要混用。
BUNDLE_MANIFEST = "runtime-manifest.json"

#: 清单结构版本。改字段形状必须同时改这个数，否则消费方无法区分新旧。
SCHEMA = 1

#: 哈希分块大小（1 MiB）。运行时目录含 GB 级资产，必须流式读，不能 `read_bytes()`。
_HASH_CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    """流式计算文件 SHA-256（与 `core/runtime_manifest._sha256` 同一分块口径）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def build_bundle_manifest(runtime_root: Path, *, chrome: str = "") -> dict[str, Any]:
    """扫描 `runtime_root`，返回 `runtime-manifest.json` 的内容（不落盘）。"""
    root = Path(runtime_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"运行时目录不存在: {root}")
    entries: list[dict[str, Any]] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        # 跳过清单自身：否则重跑制备脚本时清单把自己的哈希记进去，内容永远不同。
        if path == root / BUNDLE_MANIFEST:
            continue
        size = path.stat().st_size
        total += size
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": size,
                "sha256": _sha256(path),
            }
        )
    return {
        "schema": SCHEMA,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "chrome": chrome,
        "files": len(entries),
        "bytes": total,
        "sha256": entries,
    }


def write_bundle_manifest(runtime_root: Path, *, chrome: str = "") -> dict[str, Any]:
    """生成并落盘 `runtime-manifest.json`（先写 `.part` 再原子改名，沿用构建脚本纪律）。"""
    root = Path(runtime_root).resolve()
    manifest = build_bundle_manifest(root, chrome=chrome)
    target = root / BUNDLE_MANIFEST
    staged = target.with_name(target.name + ".part")
    staged.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(staged, target)
    return manifest


def _command_manifest(args: argparse.Namespace) -> int:
    manifest = write_bundle_manifest(Path(args.runtime_root), chrome=args.chrome)
    print(
        f"runtime manifest written: {Path(args.runtime_root) / BUNDLE_MANIFEST} "
        f"(files={manifest['files']}, bytes={manifest['bytes']})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="三平台构建脚本的公共逻辑")
    sub = parser.add_subparsers(dest="command", required=True)

    manifest_parser = sub.add_parser("manifest", help=f"生成运行时目录的 {BUNDLE_MANIFEST}")
    manifest_parser.add_argument("--runtime-root", required=True, help="运行时目录（会被扫描）")
    manifest_parser.add_argument("--chrome", default="", help="Chromium 可执行文件路径（写进清单）")
    manifest_parser.set_defaults(handler=_command_manifest)

    args = parser.parse_args(argv)
    handler: Any = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover - argparse 的 required=True 已挡住
        parser.error("缺少子命令")
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
