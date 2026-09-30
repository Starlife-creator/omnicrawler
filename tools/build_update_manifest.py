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
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from omnicrawler.core.versions import version_key  # noqa: E402
from omnicrawler.services.update_feed import (  # noqa: E402
    FEED_FILENAME,
    canonical_feed_bytes,
    feed_filename,
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
        _reject_protected_top_level(relative.as_posix(), origin=str(path))
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        files[relative.as_posix()] = path
    if not files:
        raise SystemExit(f"载荷目录里没有任何文件: {root}")
    return files


def _reject_protected_top_level(relative: str, *, origin: str) -> None:
    """载荷里出现 ``work/`` ``data/`` 等受保护顶层 ⇒ 直接拒绝（而不是默默漏掉）。

    这些目录是**用户数据**。它们出现在载荷里说明打包步骤把用户目录一起装进去了，
    静默跳过会让清单与实际归档不一致，正是"看着正常、其实错了"的典型。
    """
    top = relative.split("/", 1)[0]
    if top.lower() in _FORBIDDEN_TOP_LEVEL:
        raise SystemExit(
            f"载荷里出现受保护顶层路径 {top}/（{origin}）—— "
            "构建产物不该包含用户数据目录，请检查打包步骤"
        )


def _sha256_stream(handle: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _hash_archive_members(archive: Path) -> dict[str, tuple[str, int]]:
    """从**归档本体**流式算逐文件哈希，返回 ``去掉顶层目录的相对 posix 路径 → (sha256, 体积)``。

    为什么不从构建暂存目录算：客户端下载的是归档，清单必须与**归档里的字节**逐字节一致。
    从暂存目录算就多了一条"打包时排除了什么"的隐含约定（tar 排除了 ``OmniCrawler/logs``、
    zip 排除了顶层 ``logs/``…），两处一旦不同步，增量校验就会永远失败，而且失败得很晚
    （用户侧才发现）。直接读归档 = 这条约定不存在。

    对 tar.xz 是**流式**（顺序读，逐成员算完即丢），内存恒定，不落第二个解压目录。
    """
    if not archive.is_file():
        raise SystemExit(f"载荷归档不存在: {archive}")
    members: dict[str, tuple[str, int]] = {}
    roots: set[str] = set()

    def _record(name: str, digest: str, size: int) -> None:
        parts = [part for part in name.split("/") if part not in ("", ".")]
        if len(parts) < 2:
            raise SystemExit(f"归档成员不在顶层目录内（无法剥根）: {name}")
        roots.add(parts[0])
        relative = "/".join(parts[1:])
        if not relative:
            return
        _reject_protected_top_level(relative, origin=f"{archive.name}!{name}")
        members[relative] = (digest, size)

    if archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                if info.is_dir():
                    continue
                with handle.open(info) as stream:
                    _record(info.filename, _sha256_stream(stream), info.file_size)
    else:
        with tarfile.open(archive, "r:*") as handle:
            for member in handle:
                if not member.isfile():
                    continue
                stream = handle.extractfile(member)
                if stream is None:  # pragma: no cover - isfile() 为真时必有流
                    continue
                _record(member.name, _sha256_stream(stream), member.size)
    if not members:
        raise SystemExit(f"归档里没有任何文件: {archive}")
    if len(roots) != 1:
        raise SystemExit(
            f"归档顶层目录不唯一 {sorted(roots)} —— 便携包应当只有单个 <根>/ 顶层"
        )
    return members


def _load_payload(*, directory: str, archive: str, what: str) -> dict[str, tuple[str, int]]:
    """统一的载荷读取：目录（开发/回算）或归档（构建期，与发布字节一致）。"""
    if directory and archive:
        raise SystemExit(f"{what}：--payload-dir 与 --payload-archive 二选一")
    if archive:
        return _hash_archive_members(Path(archive).expanduser())
    if not directory:
        raise SystemExit(f"{what}：需要 --payload-dir 或 --payload-archive")
    return {
        relative: (_sha256_of(path), path.stat().st_size)
        for relative, path in _walk_payload(Path(directory).expanduser()).items()
    }


def _parse_asset(spec: str) -> tuple[str, Path]:
    key, _, raw = spec.partition("=")
    if not key or not raw:
        raise SystemExit(f"--asset/--delta 需要 <键>=<文件路径> 形式，收到: {spec}")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise SystemExit(f"资源文件不存在: {path}")
    return key, path


def _resolve_notes(args: argparse.Namespace) -> str:
    """取变更摘要：``--notes-file`` 优先（UTF-8），否则 ``--notes``。

    为什么要文件：Windows PowerShell 5.1 把**非 ASCII 参数**交给原生 exe 时会按控制台
    ANSI 代码页转码，中文会变成 ``??`` —— 而 CI 的 Windows 构建正是 ``powershell -File``
    跑的。走文件就没有这条 argv 编码路径。
    """
    path = str(getattr(args, "notes_file", "") or "")
    if path:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    return str(args.notes or "")


def build(args: argparse.Namespace) -> dict[str, object]:
    if getattr(args, "no_payload", False):
        return _build_assets_only(args)
    notes = _resolve_notes(args)
    payload_files: dict[str, dict[str, object]] = {}
    total_bytes = 0
    if not getattr(args, "no_payload", False):
        hashed = _load_payload(
            directory=str(args.payload_dir or ""),
            archive=str(getattr(args, "payload_archive", "") or ""),
            what="载荷",
        )
        for relative, (digest, size) in hashed.items():
            total_bytes += size
            payload_files[relative] = {"sha256": digest, "size": size}

    deleted: list[str] = []
    if args.previous_payload_dir or getattr(args, "previous_payload_archive", ""):
        previous = _load_payload(
            directory=str(args.previous_payload_dir or ""),
            archive=str(getattr(args, "previous_payload_archive", "") or ""),
            what="上一版载荷",
        )
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

    platform, edition = _target(args)

    document: dict[str, object] = {
        "version": str(args.version).strip(),
        "published_at": args.published_at
        or datetime.now(UTC).replace(microsecond=0).isoformat(),
        "notes": notes,
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
    if platform:
        # 客户端会**交叉校验**这个字段（防把别的平台的清单应用上来）
        document["platform"] = platform
    if edition:
        # 同理：Standard/Full 的逐文件清单不同，套错版本会白下或漏文件
        document["edition"] = edition
    if not version_key(str(document["version"])):
        raise SystemExit(f"--version 不可比较: {document['version']!r}")

    source = str(getattr(args, "payload_archive", "") or args.payload_dir or "")
    print(f"[清单] 载荷文件 {len(payload_files)} 个，合计 {total_bytes} 字节（源: {source}）")
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


def _build_assets_only(args: argparse.Namespace) -> dict[str, object]:
    """只声明 ``assets`` 的清单（无逐文件清单）⇒ 该平台走**全量更新**。

    为什么需要：某些平台的载荷不便解压（macOS 的 ``.dmg``），而"逐文件清单"必须遍历载荷
    才能算哈希。与其让那个平台**完全没有清单**（客户端直接报错），不如出一份"只有整包"的
    清单 —— 全量路径本来就是 0.14.0 用户唯一可走的路。
    """
    assets: dict[str, dict[str, object]] = {}
    for spec in args.asset or []:
        key, path = _parse_asset(str(spec))
        body = path.read_bytes()
        assets[key] = {
            "name": path.name,
            "sha256": hashlib.sha256(body).hexdigest(),
            "size": len(body),
        }
    if not assets:
        raise SystemExit("--no-payload 时至少要给一个 --asset（否则该平台没有任何可下载内容）")
    platform, edition = _target(args)
    document: dict[str, object] = {
        "version": str(args.version).strip(),
        "published_at": args.published_at
        or datetime.now(UTC).replace(microsecond=0).isoformat(),
        "notes": _resolve_notes(args),
        "assets": assets,
    }
    if platform:
        document["platform"] = platform
    if edition:
        document["edition"] = edition
    print(f"[清单] 无逐文件清单（--no-payload）⇒ 该平台走全量更新；整包资源 {len(assets)} 个")
    return document


def _target(args: argparse.Namespace) -> tuple[str, str]:
    """归一化并校验 (``--platform``, ``--edition``) 与其 ``--asset`` 键的一致性。"""
    platform = str(args.platform or "").strip().lower()
    edition = str(getattr(args, "edition", "") or "").strip().lower()
    if edition and not platform:
        raise SystemExit("[错误] --edition 只能与 --platform 一起给出（文件名要平台+版本两段）")
    if not platform:
        return "", ""
    wanted = f"{platform}-{edition}" if edition else ""
    for spec in args.asset or []:
        key = str(spec).split("=", 1)[0].strip()
        if not key.startswith(f"{platform}-"):
            raise SystemExit(
                f"[错误] --asset 的键 {key!r} 与 --platform {platform!r} 不匹配"
                f"（应为 {platform}-<edition>）"
            )
        if wanted and key != wanted:
            raise SystemExit(
                f"[错误] 声明了 --edition {edition} ⇒ --asset 的键必须是 {wanted!r}，收到 {key!r}"
            )
    return platform, edition


def main() -> int:
    parser = argparse.ArgumentParser(description="生成并签名应用自更新源文档（维护者本机运行）")
    parser.add_argument(
        "--payload-dir",
        default=None,
        help="解压后的载荷目录（新版本）；与 --payload-archive 二选一",
    )
    parser.add_argument(
        "--payload-archive",
        default=None,
        help=(
            "载荷**归档本体**（zip / tar.xz / tar.gz）——**构建脚本用这个**。"
            "逐文件哈希直接从归档流式算（不落第二个解压目录，tar.xz 内存恒定），"
            "于是清单与用户下载到的字节**逐字节一致**，不需要再约定"
            "「打包时排除了什么」。顶层目录会被自动剥掉（便携包的 OmniCrawler/）。"
        ),
    )
    parser.add_argument(
        "--previous-payload-dir", default="", help="上一版载荷目录（给了才输出「已移除」清单）"
    )
    parser.add_argument(
        "--previous-payload-archive", default="", help="上一版载荷归档（同上，二者选一）"
    )
    parser.add_argument("--version", required=True, help="目标版本号，如 0.15.0")
    parser.add_argument(
        "--no-payload",
        action="store_true",
        help=(
            "不产出逐文件清单（只声明 assets/notes）⇒ 该平台走**全量更新**。"
            "用于载荷不便解压的平台（如 macOS 的 .dmg），或本版不做增量时。"
        ),
    )
    parser.add_argument(
        "--platform",
        default=None,
        choices=("windows", "linux", "macos"),
        help=(
            "本清单描述的**平台**（写入 platform 字段；客户端会校验，防止把别的平台的清单"
            "应用上来）。给了它时 --asset 的键必须是 <platform>-<edition>，且 --out 缺省为 "
            "update-<platform>.json。三平台各出一份清单（载荷不同，一份清单只可能描述一个平台）。"
        ),
    )
    parser.add_argument(
        "--edition",
        default="",
        choices=("", "standard", "full"),
        help=(
            "本清单描述的**版本**（Standard/Full，写入 edition 字段；客户端会校验）。给了它时 "
            "--out 缺省为 update-<platform>-<edition>.json，且 --asset 的键必须是 "
            "<platform>-<edition>。两版的 runtime/OCR 载荷不同 ⇒ 逐文件清单也不同，"
            "所以一份清单只可能描述一个 (平台, 版本)。"
        ),
    )
    parser.add_argument("--notes", default="", help="给用户看的变更摘要")
    parser.add_argument(
        "--notes-file",
        default="",
        help=(
            "从 **UTF-8 文件**读变更摘要（优先于 --notes）。构建脚本用这个："
            "Windows PowerShell 5.1 传非 ASCII 参数给原生 exe 会按控制台代码页转码，"
            "中文会变成 ??；走文件就没有这条转码路径。"
        ),
    )
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
    selected_platform = str(getattr(args, "platform", "") or "").strip().lower()
    selected_edition = str(getattr(args, "edition", "") or "").strip().lower()
    default_name = (
        feed_filename(selected_platform, selected_edition) if selected_platform else FEED_FILENAME
    )
    out = Path(args.out or default_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[完成] 已写出 {out}（{out.stat().st_size} 字节）")
    print("       发布时把它放到更新源基址下（与它声明的载荷基址一致），客户端即按此校验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
