#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""生成并签名应用自更新的更新源文档（``update.json``）——**仅维护者本机运行**。

它把"发布一次更新"所需要的三样东西一次做齐：

1. **逐文件清单**（从**解压后的载荷**目录算出每个文件的 sha256 与体积）——
   客户端据此只下载与本地不同的文件（"自动跳过一样的"全部依据就在这里）；
2. **删除清单**（给了 ``--previous-payload-dir`` 时自动算出：上一版有、本版没有的路径）
   —— 逐文件差异必须显式声明删除，否则旧文件会永远残留；
3. **签名**（``--key 冷私钥路径``，ed25519；规范化方式与升级包 ``upgrade.json``
   逐字一致，故客户端用同一把公钥即可验两样）。

冷密钥铁律：本工具**只把私钥当参数读入内存用于签名**，绝不打印、绝不复制、绝不入仓。
建议把它当作一次**本地半自动发布动作**（与市场侧的 `sign_catalog.py` 同一定位）。

用法::

    python tools/build_update_manifest.py \\
        --payload-dir dist/OmniCrawler-0.15.0-Windows-Portable-Standard \\
        --previous-payload-dir dist/OmniCrawler-0.14.0-Windows-Portable-Standard \\
        --version 0.15.0 \\
        --asset windows-standard=dist/OmniCrawler-0.15.0-Windows-Portable-Standard.zip \\
        --delta 0.14.0=dist/update-0.14.0-to-0.15.0.zip \\
        --key "C:\\path\\to\\update_signing_private.pem" \\
        --out dist/update.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from omnicrawler.core.versions import version_key  # noqa: E402
from omnicrawler.services.update_feed import (  # noqa: E402
    FEED_FILENAME,
    canonical_feed_bytes,
)

#: 绝不进清单的顶层目录（用户数据 / 标记）——载荷来自构建产物，本不该含它们；
#: 真含了说明打包错了，这里直接拒绝而不是默默漏掉。
_FORBIDDEN_TOP_LEVEL = {
    "work",
    "data",
    "output",
    "logs",
    ".omnicrawler",
    "PORTABLE.flag",
    "portable.flag",
}

#: 构建垃圾，不参与清单（否则每次构建都会产生"变化"）
_SKIP_SUFFIXES = {".pyc", ".pyo"}
_SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_payload(root: Path) -> dict[str, Path]:
    """遍历载荷，返回 ``相对 posix 路径 → 文件``（跳过构建垃圾，拒绝受保护顶层）。"""
    if not root.is_dir():
        raise SystemExit(f"载荷目录不存在: {root}")
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0].lower() in _FORBIDDEN_TOP_LEVEL:
            raise SystemExit(
                f"载荷里出现受保护顶层路径 {relative.parts[0]}/（{path}）—— "
                "构建产物不该包含用户数据目录，请检查打包步骤"
            )
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        files[relative.as_posix()] = path
    if not files:
        raise SystemExit(f"载荷目录里没有任何文件: {root}")
    return files


def _parse_asset(spec: str) -> tuple[str, Path]:
    key, _, raw = spec.partition("=")
    if not key or not raw:
        raise SystemExit(f"--asset/--delta 需要 <键>=<文件路径> 形式，收到: {spec}")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise SystemExit(f"资源文件不存在: {path}")
    return key, path


def build(args: argparse.Namespace) -> dict[str, object]:
    payload_dir = Path(args.payload_dir).expanduser()
    files = _walk_payload(payload_dir)

    payload_files: dict[str, dict[str, object]] = {}
    total_bytes = 0
    for relative, path in files.items():
        size = path.stat().st_size
        total_bytes += size
        payload_files[relative] = {"sha256": _sha256_of(path), "size": size}

    deleted: list[str] = []
    if args.previous_payload_dir:
        previous = _walk_payload(Path(args.previous_payload_dir).expanduser())
        deleted = sorted(set(previous) - set(payload_files))

    assets: dict[str, dict[str, object]] = {}
    for spec in args.asset or []:
        key, path = _parse_asset(spec)
        assets[key] = {
            "name": path.name,
            "sha256": _sha256_of(path),
            "size": path.stat().st_size,
        }

    delta: dict[str, dict[str, object]] = {}
    for spec in args.delta or []:
        key, path = _parse_asset(spec)
        if not version_key(key):
            raise SystemExit(f"--delta 的旧版本号不可比较: {key}")
        delta[key] = {
            "name": path.name,
            "sha256": _sha256_of(path),
            "size": path.stat().st_size,
        }

    full_fallback: dict[str, object] | None = None
    if args.full_fallback:
        fb_version, fb_base, fb_name, fb_sha, fb_size = args.full_fallback
        full_fallback = {
            "version": fb_version,
            "base": fb_base,
            "name": fb_name,
            "sha256": fb_sha,
            "size": int(fb_size),
        }

    document: dict[str, object] = {
        "version": str(args.version).strip(),
        "published_at": args.published_at
        or datetime.now(UTC).replace(microsecond=0).isoformat(),
        "notes": args.notes or "",
        "assets": assets,
        "payload": {
            "base_url": args.base_url or "",
            "files": payload_files,
            "delta": delta,
            "deleted": deleted,
        },
    }
    if full_fallback is not None:
        document["full_fallback"] = full_fallback
    if not version_key(str(document["version"])):
        raise SystemExit(f"--version 不可比较: {document['version']!r}")

    print(f"[清单] 载荷文件 {len(payload_files)} 个，合计 {total_bytes} 字节（{payload_dir}）")
    if deleted:
        print(f"[清单] 本版已移除 {len(deleted)} 个路径（相对上一版）")
    if assets:
        print(f"[清单] 整包资源 {len(assets)} 个：{', '.join(sorted(assets))}")
    if delta:
        print(f"[清单] 变更包 {len(delta)} 个（相对：{', '.join(sorted(delta))}）")
    if full_fallback is not None:
        print(f"[清单] 全量兜底指向 v{full_fallback['version']}（本版未重建全量包）")
    return document


def sign(document: dict[str, object], key_path: Path) -> dict[str, object]:
    """ed25519 签名（规范化与 `upgrade.json` 逐字一致）。**不打印任何密钥材料。**"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pem = key_path.expanduser().read_bytes()
    private_key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SystemExit("签名密钥必须是 ed25519 私钥（PEM）")
    signature = base64.b64encode(private_key.sign(canonical_feed_bytes(document))).decode()
    public_raw = private_key.public_key().public_bytes_raw()
    from omnicrawler.plugins.identity import derive_fingerprint

    print(f"[签名] 已用指纹 {derive_fingerprint(public_raw)} 的密钥签名（{key_path}）")
    return {**document, "signature": signature}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成并签名应用自更新源文档（维护者本机运行）")
    parser.add_argument("--payload-dir", required=True, help="解压后的载荷目录（新版本）")
    parser.add_argument(
        "--previous-payload-dir", default="", help="上一版载荷目录（给了才输出「已移除」清单）"
    )
    parser.add_argument("--version", required=True, help="目标版本号，如 0.15.0")
    parser.add_argument("--notes", default="", help="给用户看的变更摘要")
    parser.add_argument("--published-at", default="", help="发布时间（缺省＝当前 UTC）")
    parser.add_argument("--base-url", default="", help="载荷基址（缺省＝feed 基址）")
    parser.add_argument("--asset", action="append", help="整包资源：<平台-版本>=<文件>")
    parser.add_argument("--delta", action="append", help="变更包：<旧版本号>=<文件>")
    parser.add_argument(
        "--full-fallback", nargs=5, default=None,
        metavar=("VERSION", "BASE", "NAME", "SHA256", "SIZE"),
        help="本版不重建全量包时：指向最近一次带全量包的发布（老版本用户从这里取全量）",
    )
    parser.add_argument(
        "--key",
        default=None,
        help="ed25519 冷私钥 PEM 路径（只读入内存签名）；与 --emit-unsigned 二选一",
    )
    parser.add_argument(
        "--emit-unsigned",
        default=None,
        metavar="PATH",
        help=(
            "只产出**无签名清单**（CI 用；不需要任何密钥）。逐文件哈希由 CI 算好作数据，"
            "签名留给维护者本机（见 tools/sign_update_manifest.py）——这样每版发布"
            "不必再把整包搬到本机。"
        ),
    )
    parser.add_argument("--out", default="", help=f"输出文件名（缺省 {FEED_FILENAME}）")
    args = parser.parse_args()

    if bool(args.emit_unsigned) == bool(args.key):
        print(
            "[错误] --emit-unsigned 与 --key 必须**二选一**："
            "CI 用前者产无签名清单，维护者本机用后者直接签名。",
            file=sys.stderr,
        )
        return 2

    if args.emit_unsigned:
        unsigned = build(args)
        out = Path(args.emit_unsigned)
        out.parent.mkdir(parents=True, exist_ok=True)
        # 规范化写入：与签名时**同一份字节口径**，避免"签的和发的不一致"
        out.write_text(
            json.dumps(unsigned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"[完成] 已写出**无签名**清单 {out}（{out.stat().st_size} 字节）")
        print("       ★ 它不能直接对外发布：客户端要求 signature。")
        print("       ★ 维护者本机执行：python tools/sign_update_manifest.py "
              f"--unsigned {out} --key <冷私钥> --out update.json")
        return 0

    document = sign(build(args), Path(args.key))
    out = Path(args.out or FEED_FILENAME)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[完成] 已写出 {out}（{out.stat().st_size} 字节）")
    print("       发布时把它放到更新源基址下（与它声明的载荷基址一致），客户端即按此校验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
