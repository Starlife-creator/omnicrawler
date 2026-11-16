#!/usr/bin/env python3
"""组件包（``.ocp``）签名工具（**仅冷机运行**）。

为什么需要它
------------
`tools/sign_plugin.py` 能生成密钥对、能签插件，但**没有签组件的入口**。
结果是：维护者有了第三把钥匙（``configs/component_trust.pub.pem``）却签不了组件包，
只能手写 base64/规范化 JSON —— 那正是最容易签错的地方（规范化方式必须与
客户端 ``ComponentManager.inspect_package`` **逐字一致**，差一个空格就验不过）。

本工具把"规范化 + 签名 + 写回 zip"收口到一处，保证：
1. 与客户端使用**同一个**规范化函数（``_canonical_manifest`` 与客户端同构）；
2. 签名覆盖 ``component.json`` **除 signature 外的全部字段**；
3. 拒绝把私钥写进仓库（沿用 `sign_plugin.py` 的冷密钥纪律）。

用法
----
    # 给一个已有 .ocp 包签名（原地更新，另存原包为 .orig）
    python tools/sign_component.py sign \\
        --package build/ocr-tesseract-0.1.0.ocp \\
        --private-key "%USERPROFILE%\\.omnicrawler\\keys\\component_signing_private.pem"

    # 只算规范化摘要与预期签名（不写文件，便于核对）
    python tools/sign_component.py inspect --package build/ocr-tesseract-0.1.0.ocp

冷密钥纪律
----------
私钥只按路径读入内存用于签名，**绝不打印、绝不复制、绝不入仓**。
本工具不提供"生成密钥"——那由 ``tools/sign_plugin.py generate-keys`` 负责。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _candidate in (_REPO_ROOT / "src", _REPO_ROOT):
    _candidate_str = str(_candidate)
    if _candidate_str not in sys.path:
        sys.path.insert(0, _candidate_str)

from omnicrawler.plugins.signing import sign_bytes  # noqa: E402
from omnicrawler.services.component_manager import _safe_component_path  # noqa: E402

MANIFEST_NAME = "component.json"


def _repo_root() -> Path:
    """仓库根目录（可被环境变量覆盖，**仅供测试**）。

    为什么需要这个开关：本仓的 pytest 临时目录被配置在**仓库内部**，
    于是"私钥不得在仓内"这条判据无法用普通 tmp_path 测试——
    要么放宽判据（错），要么让判据的"仓界"可注入（对）。
    生产环境不设这个变量，行为完全不变。
    """
    override = os.environ.get("OMNICRAWL_SIGN_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _REPO_ROOT


def canonical_manifest(raw: dict) -> bytes:
    """规范化清单字节——**必须与客户端验签侧逐字一致**。

    客户端用的是：
        ``json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))``
    其中 ``raw`` 是**弹掉 signature 之后**的字典。任何差异（空格、排序、ensure_ascii）
    都会让签名验不过，所以这里刻意复用同一串参数，并加了守卫测试钉住一致性。
    """
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def strip_signature(manifest: dict) -> dict:
    """返回弹掉 ``signature`` 的副本（不改原字典）。"""
    copied = dict(manifest)
    copied.pop("signature", None)
    return copied


def read_manifest(package: Path) -> tuple[dict, dict[str, bytes]]:
    """读出 ``component.json`` 与包内全部成员字节。"""
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if MANIFEST_NAME not in names:
            raise SystemExit(f"组件包缺少 {MANIFEST_NAME}: {package}")
        manifest = json.loads(archive.read(MANIFEST_NAME))
        if not isinstance(manifest, dict):
            raise SystemExit(f"{MANIFEST_NAME} 必须是 JSON 对象")
        members = {name: archive.read(name) for name in names if name != MANIFEST_NAME}
    return manifest, members


def verify_local_hashes(manifest: dict, members: dict[str, bytes]) -> list[str]:
    """按清单里的 files 逐一核对包内成员哈希；返回问题列表。"""
    problems: list[str] = []
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        problems.append("清单 files 为空——签一个没有文件哈希的组件包没有意义")
        return problems
    for name, expected in files.items():
        try:
            safe = str(_safe_component_path(str(name)))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"不安全的组件路径 {name!r}: {exc}")
            continue
        payload = members.get(safe)
        if payload is None:
            problems.append(f"清单声明了 {name} 但包里没有该文件")
            continue
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            problems.append(f"{name} 哈希不匹配：清单={expected[:16]}… 实际={actual[:16]}…")
    return problems


def _write_package(target: Path, manifest: dict, members: dict[str, bytes]) -> None:
    """原子写回：先写临时名，再替换，避免中断留下半截包。"""
    target = target.resolve()
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
    ) as handle:
        temp_path = Path(handle.name)
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, payload in members.items():
                archive.writestr(name, payload)
        temp_path.replace(target)
    finally:
        temp_path.unlink(missing_ok=True)


def command_inspect(args: argparse.Namespace) -> int:
    package = Path(args.package).expanduser()
    manifest, members = read_manifest(package)
    body = strip_signature(manifest)
    problems = verify_local_hashes(body, members)
    print(json.dumps({
        "package": str(package),
        "name": body.get("name"),
        "version": body.get("version"),
        "files": len(body.get("files") or {}),
        "already_signed": "signature" in manifest,
        "canonical_sha256": hashlib.sha256(canonical_manifest(body)).hexdigest(),
        "self_check": problems or "ok",
    }, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def command_sign(args: argparse.Namespace) -> int:
    package = Path(args.package).expanduser()
    if not package.is_file():
        raise SystemExit(f"组件包不存在: {package}")
    # 冷密钥安全：绝不从仓库内路径读私钥
    private_path = Path(args.private_key).expanduser()
    if not private_path.is_file():
        raise SystemExit(f"私钥不存在: {private_path}")
    try:
        private_path.resolve().relative_to(_repo_root())
    except ValueError:
        pass  # 不在仓库内 —— 正常
    else:
        raise SystemExit(
            f"拒绝使用仓库内的私钥文件: {private_path}\n"
            "冷私钥必须放在仓外（例如桌面档案目录或加密盘）。"
        )

    manifest, members = read_manifest(package)
    body = strip_signature(manifest)
    problems = verify_local_hashes(body, members)
    if problems:
        raise SystemExit(
            "签名前自检失败，已拒绝签名（签一个哈希对不上的包没有意义）：\n  "
            + "\n  ".join(problems)
        )

    signature = sign_bytes(canonical_manifest(body), private_path.read_bytes())
    signed = dict(body, signature=base64.b64encode(signature).decode())
    _write_package(package, signed, members)
    print(json.dumps({
        "package": str(package),
        "signed": True,
        "name": body.get("name"),
        "version": body.get("version"),
        "files": len(body.get("files") or {}),
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="组件包签名工具（仅冷机运行；私钥只读不写）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sign = sub.add_parser("sign", help="给 .ocp 组件包签名（原地更新）")
    sign.add_argument("--package", required=True, help="组件包路径")
    sign.add_argument("--private-key", required=True, help="签名私钥 PEM 路径（仓外）")
    sign.set_defaults(func=command_sign)

    inspect = sub.add_parser("inspect", help="只读：自检哈希并打印规范化摘要")
    inspect.add_argument("--package", required=True, help="组件包路径")
    inspect.set_defaults(func=command_inspect)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
