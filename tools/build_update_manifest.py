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

#: 共享件（与 build_update_delta.py 同源）：载荷读取、保护路径、清单落盘。
#: 这些是**安全判据**（拒绝受保护顶层、与归档本体对齐），复制两份迟早漂移。
from manifest_common import (  # noqa: E402
    DEFAULT_TRUST_ROOT,
    cumulative_deleted,
    load_payload_entries,
    load_signed_manifest,
    resolve_trusted_key,
    sha256_of,
)

from omnicrawler.core.versions import version_key  # noqa: E402
from omnicrawler.services.update_feed import (  # noqa: E402
    AUTO_APPLY_PLATFORMS,
    FEED_FILENAME,
    canonical_feed_bytes,
    feed_filename,
)


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
    hashed = load_payload_entries(
        directory=str(args.payload_dir or ""),
        archive=str(getattr(args, "payload_archive", "") or ""),
        what="载荷",
    )
    for relative, entry in hashed.items():
        total_bytes += entry.size
        payload_files[relative] = {"sha256": entry.sha256, "size": entry.size}

    deleted: list[str] = []
    if args.previous_payload_dir or getattr(args, "previous_payload_archive", ""):
        previous = load_payload_entries(
            directory=str(args.previous_payload_dir or ""),
            archive=str(getattr(args, "previous_payload_archive", "") or ""),
            what="上一版载荷",
        )
        deleted = sorted(set(previous) - set(payload_files))
    previous_manifests = list(getattr(args, "previous_manifest", None) or [])
    previous_file = str(getattr(args, "previous_manifest_file", "") or "")
    if previous_file:
        # 构建脚本用它：`build_update_delta.py --emit-previous-args` 写出 `<旧版本>=<清单路径>` 行。
        # 走文件的原因同 `--delta-file`：基线个数是动态的。
        for line in Path(previous_file).expanduser().read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                previous_manifests.append(stripped)
    if previous_manifests:
        # ★ 用**已签名清单**算"已移除"：比"上一版解压目录"更省（几十~几百 KB，不必搬整包），
        #   而且能一次覆盖多个基线（取并集，见 cumulative_deleted 的注释）。
        trusted = resolve_trusted_key(str(getattr(args, "trusted_public_key", "") or ""))
        feeds = [
            load_signed_manifest(spec, trusted_public_key=trusted)[1]
            for spec in previous_manifests
        ]
        deleted = sorted(
            set(deleted) | set(cumulative_deleted(feeds, new_files=set(payload_files)))
        )

    assets: dict[str, dict[str, object]] = {}
    for spec in args.asset or []:
        key, path = _parse_asset(spec)
        assets[key] = {
            "name": path.name,
            "sha256": sha256_of(path),
            "size": path.stat().st_size,
        }

    delta: dict[str, dict[str, object]] = {}
    delta_specs = list(args.delta or [])
    delta_file = str(getattr(args, "delta_file", "") or "")
    if delta_file:
        # 构建脚本用它：`build_update_delta.py --emit-args` 写出 `<旧版本>=<包路径>` 行。
        # 为什么走文件而不是 argv：基线个数是**动态的**（取决于 K 与哪些清单真的取到了），
        # 而 shell 侧拼动态个数的参数在 bash 3.2 / PowerShell 5.1 上各有各的坑。
        for line in Path(delta_file).expanduser().read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                delta_specs.append(stripped)
    for spec in delta_specs:
        key, path = _parse_asset(spec)
        if not version_key(key):
            raise SystemExit(f"--delta 的旧版本号不可比较: {key}")
        delta[key] = {
            "name": path.name,
            "sha256": sha256_of(path),
            "size": path.stat().st_size,
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
    if platform:
        # 客户端会**交叉校验**这个字段（防把别的平台的清单应用上来）
        document["platform"] = platform
        # ★ 平台能力声明（与客户端的 `AUTO_APPLY_PLATFORMS` **同一判据**，不另抄一份）：
        #   macOS 的自动落地做不到（主产物是 dmg、browsers/ 在 .app 之外、ad-hoc 签名），
        #   所以清单如实写 `auto_apply: false` ⇒ 客户端只提示、引导手动安装。
        document["auto_apply"] = platform in AUTO_APPLY_PLATFORMS
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
        "--delta-file",
        default="",
        help=(
            "从文件读变更包列表（每行 `<旧版本号>=<文件>`，`#` 开头忽略）。"
            "供构建脚本消费 `build_update_delta.py --emit-args` 的产出 —— "
            "基线个数是动态的，让 shell 拼动态个数的参数在 bash 3.2 / PS 5.1 上各有坑。"
        ),
    )
    parser.add_argument(
        "--previous-manifest",
        action="append",
        default=None,
        metavar="<旧版本>=<文件路径|URL>",
        help=(
            "上一版**已签名清单**（可重复；本机路径或 URL）。用它算 `payload.deleted`："
            "`(上一版 files ∪ 上一版 deleted) − 本版 files` —— 取并集是**累积**语义，"
            "否则从更老版本跳上来的用户会留下早已删过的残留。"
        ),
    )
    parser.add_argument(
        "--full-fallback", nargs=5, default=None,
        metavar=("VERSION", "BASE", "NAME", "SHA256", "SIZE"),
        help="本版不重建全量包时：指向最近一次带全量包的发布（老版本用户从这里取全量）",
    )
    parser.add_argument(
        "--previous-manifest-file",
        default="",
        help=(
            "从文件读基线清单列表（每行 `<旧版本>=<清单路径>`）。"
            "供构建脚本消费 `build_update_delta.py --emit-previous-args` 的产出（个数动态）。"
        ),
    )
    parser.add_argument(
        "--trusted-public-key",
        default="",
        help=(
            f"更新信任根（缺省用随包内置的 {DEFAULT_TRUST_ROOT}）。"
            "只有 --previous-manifest 需要它 —— 基线清单必须验签，"
            "否则等于让中间人决定用户该下什么。"
        ),
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
