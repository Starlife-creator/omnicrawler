#!/usr/bin/env python
"""Offline plugin signing tool — run ONLY on the cold signing host.

This tool is intentionally NOT part of the runtime/portable build. It is used
by the operator on an isolated machine to:

- ``generate-keys`` : create an ed25519 keypair. The PRIVATE key is written to
  the operator-specified cold-storage location and must be moved away
  immediately; the PUBLIC key is safe to ship (it becomes the trust root).
- ``sign``          : produce a detached ``<plugin>.sig`` for a plugin file.
- ``verify``        : check a plugin's detached signature against a trust root.

Cold-key principle: the private key is generated at a location the operator can
immediately cut to cold storage; it must never enter the repo, build outputs,
or the portable zip.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# -- 自引导：让工具在直接用 `python tools/sign_plugin.py` 时也能找到 omnicrawl --
# 即便没有可编辑安装（如全新 checkout），只要运行它的 Python 装了 cryptography 即可。
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _cand in (_REPO_ROOT / "src", _REPO_ROOT):
    _cand_str = str(_cand)
    if _cand_str not in sys.path:
        sys.path.insert(0, _cand_str)

from omnicrawl.plugins.signing import (
    generate_keypair,
    sign_file,
    verify_plugin,
)

# Designated private-key generation location (operator moves it to cold storage
# immediately after generation). Override with --private-out.
PRIVATE_DEFAULT = r"C:\Users\Lenovo\Desktop\档案\隐私\plugin_signing_private.pem"
PUBLIC_DEFAULT = "configs/plugin_trust.pub.pem"


def _generate_keys(private_out: Path, public_out: Path) -> None:
    private_pem, public_pem = generate_keypair()
    private_out.write_bytes(private_pem)
    public_out.write_bytes(public_pem)
    print(f"[冷密钥] 私钥已写入: {private_out}")
    print("[冷密钥] 请立即将其剪切至加密 U 盘/离线机，并安全擦除源位置（覆写、不走回收站）。")
    print(f"[信任根] 公钥已写入（可随构建分发）: {public_out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线插件签名工具（仅冷机器运行）")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-keys", help="生成 ed25519 密钥对")
    gen.add_argument("--private-out", default=PRIVATE_DEFAULT, help="私钥输出路径（冷存储）")
    gen.add_argument("--public-out", default=PUBLIC_DEFAULT, help="公钥输出路径（信任根）")

    sg = sub.add_parser("sign", help="对插件文件签名（生成 <plugin>.sig）")
    sg.add_argument("plugin", help="插件 .py 文件路径")
    sg.add_argument("--private-key", default=PRIVATE_DEFAULT, help="签名私钥 PEM 路径")

    vf = sub.add_parser("verify", help="验签插件")
    vf.add_argument("plugin", help="插件 .py 文件路径")
    vf.add_argument("--trust", required=True, help="信任根公钥 PEM 或公钥文件路径")

    args = parser.parse_args(argv)

    if args.command == "generate-keys":
        _generate_keys(Path(args.private_out).expanduser(), Path(args.public_out).expanduser())
        return 0
    if args.command == "sign":
        private_pem = Path(args.private_key).expanduser().read_bytes()
        sig = sign_file(args.plugin, private_pem)
        print(f"已签名: {sig}")
        return 0
    if args.command == "verify":
        ok, reason = verify_plugin(args.plugin, args.trust)
        print(("OK: " if ok else "FAIL: ") + reason)
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
