#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""给 CI 产出的**无签名更新清单**签名（维护者本机 · 冷私钥）。

为什么把"算哈希"与"签名"拆开
------------------------------
清单里每个文件的 sha256 需要**一个已解压的载荷目录**才算得出来。过去这只能在维护者
本机做 ⇒ 每版发布都要先把 515MB/1.9GB 的便携包搬到本机（下载→解压→签名→上传）。

现在改成：**CI 先把无签名清单算好当数据发**（CI 能访问构建产物，且**不需要任何密钥**），
维护者本机只做签名 —— 每次发布只剩一条命令。私钥仍然**只在冷存储与本机内存**里出现，
绝不进仓库、绝不进 CI（红线不变）。

用法
----
    # ★ 每版发布的那一条命令：一次签完 (三平台 × 两版本) 的全组合
    python tools/sign_update_manifest.py \\
        --unsigned-url-base https://github.com/<owner>/<repo>/releases/download/vX.Y.Z \\
        --platforms windows,linux,macos --editions standard,full \\
        --key "C:\\path\\to\\update_signing_private.pem" --out-dir .

    # 也可以先自己下载，再对单个本地文件签名
    python tools/sign_update_manifest.py --unsigned update-linux-standard.unsigned.json \\
        --key <冷私钥> --out update-linux-standard.json

签名后把这些 ``update-<平台>[-<版本>].json`` 作为 Release 资产上传（与便携包并列）；
客户端按 ``<feed_url>/update-<平台>-<版本>.json`` 取，逐级回退到 ``update-<平台>.json``
再到 ``update.json``（见 ``commands/self_update._fetch_feed``）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from omnicrawler.services.update_feed import (  # noqa: E402
    canonical_feed_bytes,
    feed_filename,
)

#: 无签名清单的约定文件名（CI 用它挂到 Release 上）
UNSIGNED_FILENAME = "update.unsigned.json"
#: 签名后的正式文件名（客户端只认这一个）
SIGNED_FILENAME = "update.json"


def _is_not_found(exc: ValueError) -> bool:
    """区分「更新源没有这份清单」（404，可跳过）与「有但坏了」（必须报错）。

    判据只能落在 HTTP 状态码或文件状态上 —— 用错误文案里有没有 "404" 去猜属于
    「看着像」判据，一次措辞改动就会让跳过/报错的边界反过来。
    """
    if isinstance(exc.__cause__, urllib.error.HTTPError):
        return exc.__cause__.code == 404
    if isinstance(exc.__cause__, FileNotFoundError):
        return True
    return False


def load_unsigned(*, path: str | None, url: str | None) -> tuple[dict[str, Any], str]:
    """读入无签名清单（本地路径或 URL 二选一），返回 ``(文档, 来源描述)``。"""
    if bool(path) == bool(url):
        raise ValueError("--unsigned 与 --unsigned-url 必须二选一")
    if path:
        try:
            raw = Path(path).expanduser().read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(f"无签名清单不存在: {path}") from exc
        source = str(path)
    else:
        assert url is not None
        try:
            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - 维护者工具
                raw = response.read()
        except urllib.error.URLError as exc:
            raise ValueError(f"取回无签名清单失败: {exc}") from exc
        source = str(url)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"无签名清单不是合法 JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("无签名清单顶层必须是对象")
    if "signature" in document:
        # 拒绝已签名的输入：规范化字节要算在**不含 signature** 的文档上，
        # 拿一份带 signature 的文档再签会签错对象（且会掩盖"重复签名"的误操作）。
        raise ValueError(
            "输入已含 signature ⇒ 这是**已签名**清单。请对 CI 产出的 "
            f"{UNSIGNED_FILENAME} 签名，而不是对 {SIGNED_FILENAME} 再签一次。"
        )
    version = str(document.get("version") or "").strip()
    if not version:
        raise ValueError("无签名清单缺少 version（拒绝签一份来路不明的文档）")
    return document, source


def sign_document(document: dict[str, Any], key_path: Path) -> dict[str, Any]:
    """用 ed25519 冷私钥签 ``canonical_feed_bytes(document)``，返回含 signature 的文档。"""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    if not key_path.is_file():
        raise ValueError(f"冷私钥文件不存在: {key_path}")
    try:
        private_key = load_pem_private_key(key_path.read_bytes(), password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"冷私钥无法解析（无口令 PEM 预期）: {exc}") from exc
    signature = base64.b64encode(private_key.sign(canonical_feed_bytes(document))).decode()
    return {**document, "signature": signature}


def main() -> int:
    parser = argparse.ArgumentParser(description="给无签名更新清单签名（维护者本机）")
    parser.add_argument("--unsigned", default=None, help=f"无签名清单路径（CI 产的 {UNSIGNED_FILENAME}）")
    parser.add_argument("--unsigned-url", default=None, help="无签名清单 URL（与 --unsigned 二选一）")
    parser.add_argument("--key", required=True, help="ed25519 冷私钥 PEM 路径（只读入内存签名）")
    parser.add_argument("--out", default=SIGNED_FILENAME, help=f"输出文件名（缺省 {SIGNED_FILENAME}）")
    parser.add_argument(
        "--platforms",
        default=None,
        help=(
            "**一条命令签多平台**：逗号分隔（windows,linux,macos）。"
            "配合 --unsigned-url-base 使用，逐个取 update-<平台>[-<版本>].unsigned.json 并签名，"
            "输出到 --out-dir。再给 --editions 即按 (平台 × 版本) 全组合签。"
        ),
    )
    parser.add_argument(
        "--editions",
        default=None,
        help=(
            "与 --platforms 组合：逗号分隔（standard,full）。给定时签 "
            "(平台 × 版本) 的**全部组合**，文件名 update-<平台>-<版本>.json；"
            "不给则签平台级粗粒度清单（update-<平台>.json）。"
        ),
    )
    parser.add_argument(
        "--unsigned-url-base",
        default=None,
        help="Release 下载基址（与 --platforms 一起用），如 "
             "https://github.com/<owner>/<repo>/releases/download/vX.Y.Z",
    )
    parser.add_argument("--out-dir", default=".", help="--platforms 模式的输出目录")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "缺哪一份就报错退出（默认：缺的跳过并黄字提醒 —— 某平台某版本的包可能因超 2GiB "
            "没随发布，属于已知情形，不该阻塞其余清单落盘）"
        ),
    )
    args = parser.parse_args()

    if args.platforms:
        if not args.unsigned_url_base:
            print("[错误] --platforms 需要配合 --unsigned-url-base 使用", file=sys.stderr)
            return 2
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        base = args.unsigned_url_base.rstrip("/")
        platforms = [p.strip().lower() for p in str(args.platforms).split(",") if p.strip()]
        editions = [e.strip().lower() for e in str(args.editions or "").split(",") if e.strip()]
        targets = [(p, e) for p in platforms for e in (editions or [""])]
        signed_paths: list[Path] = []
        missing: list[str] = []
        for platform, edition in targets:
            name = feed_filename(platform, edition)
            label = f"{platform}-{edition}" if edition else platform
            url = f"{base}/{name.replace('.json', '.unsigned.json')}"
            try:
                document, _source = load_unsigned(path=None, url=url)
                signed = sign_document(document, Path(args.key))
            except ValueError as exc:
                if _is_not_found(exc):
                    missing.append(label)
                    print(f"[跳过] {label}: 更新源没有 {name.replace('.json', '.unsigned.json')}")
                    continue
                print(f"[错误] {label}: {exc}", file=sys.stderr)
                return 2
            target = out_dir / name
            body = (json.dumps(signed, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            target.write_bytes(body)
            signed_paths.append(target)
            files = signed.get("payload", {}).get("files") or {}
            print(f"[完成] {label}: {target}（{len(body)} 字节 · 逐文件 {len(files)} 项）")
        if not signed_paths:
            print("[错误] 一份清单都没签成 ⇒ 检查 --unsigned-url-base 与发布是否完成", file=sys.stderr)
            return 2
        if missing and args.strict:
            print(f"[错误] --strict：以下清单缺席 {missing}", file=sys.stderr)
            return 2
        print(f"       下一步：把这 {len(signed_paths)} 份 update-*.json 作为 Release 资产上传。")
        if missing:
            print(f"       ★ 未签到（更新源没有）：{', '.join(missing)}")
        return 0

    try:
        document, source = load_unsigned(path=args.unsigned, url=args.unsigned_url)
        signed = sign_document(document, Path(args.key))
    except ValueError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(signed, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    out.write_bytes(body)

    files = signed.get("payload", {}).get("files") or {}
    print(f"[完成] 已签名 {source}")
    print(f"       version = {signed.get('version')} · 逐文件清单 {len(files)} 项")
    print(f"       输出 {out}（{len(body)} 字节 · sha256={hashlib.sha256(body).hexdigest()[:16]}…）")
    print("       下一步：把该文件作为 Release 资产上传（与便携包并列），客户端即从 "
          "<feed_url>/update.json 取到它。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
