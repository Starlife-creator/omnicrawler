#!/usr/bin/env python3
"""本地身份与信任列表 CLI（插件生态，对齐 Helios 身份系统）。

子命令：
  create <username> [--password P]   创建本地身份（Ed25519 密钥对，私钥密码加密入 OS 密钥库）
  list                                列出本地身份（用户名 + 指纹）
  show <username> [--password P]      显示身份详情（公钥指纹）
  delete <username> [--password P]    注销身份（需密码确认）
  trust add --pubkey <作者的 .pem 公钥文件> --name N   信任某创作者（绑定公钥，纯本地）
  trust revoke <fingerprint>          撤销信任
  trust list                          列出信任列表

密码来源：--password > 环境变量 OMNICRAWL_IDENTITY_PASSWORD（测试/CI 用）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _cand in (_REPO_ROOT / "src", _REPO_ROOT):
    _cand_str = str(_cand)
    if _cand_str not in sys.path:
        sys.path.insert(0, _cand_str)

from omnicrawl.plugins.identity import IdentityStore  # noqa: E402
from omnicrawl.plugins.trust import TrustedUserList  # noqa: E402


def _resolve_password(password: str | None) -> str:
    if password:
        return password
    value = os.environ.get("OMNICRAWL_IDENTITY_PASSWORD", "")
    if value:
        return value
    raise ValueError("需要密码：--password 或环境变量 OMNICRAWL_IDENTITY_PASSWORD")


def cmd_create(args: argparse.Namespace) -> int:
    try:
        identity = IdentityStore().create(args.username, _resolve_password(args.password))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL 创建身份: {exc}")
        return 1
    print(f"OK 身份已创建: {identity.username}")
    print(f"   公钥指纹（生态唯一标识）: {identity.key_fingerprint}")
    print("   私钥已用密码加密存入 OS 密钥库（keyring），未落盘明文。")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = IdentityStore()
    usernames = store.list_usernames()
    if not usernames:
        print("暂无本地身份。创建: python tools/identity.py create <username>")
        return 0
    print(f"本地身份（{len(usernames)}）:")
    for username in usernames:
        try:
            identity = store.load(username, _resolve_password(args.password))
            print(f"  {username}  fingerprint={identity.key_fingerprint}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {username}  （需密码解锁: {exc}）")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        identity = IdentityStore().load(args.username, _resolve_password(args.password))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL 加载身份: {exc}")
        return 1
    print(f"用户名: {identity.username}")
    print(f"公钥指纹: {identity.key_fingerprint}")
    print(f"创建时间: {identity.created_at.isoformat()}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    try:
        IdentityStore().delete(args.username, _resolve_password(args.password))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL 删除身份: {exc}")
        return 1
    print(f"OK 身份已删除: {args.username}")
    return 0


def cmd_trust_add(args: argparse.Namespace) -> int:
    from omnicrawl.plugins.identity import CreatorIdentity, public_key_bytes_from_pem

    try:
        public_key = public_key_bytes_from_pem(args.pubkey)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL 公钥加载失败: {exc}")
        return 1
    creator = CreatorIdentity(username=args.name, public_key=public_key)
    added = TrustedUserList().add(creator, source="manual", path_hint=f"（{args.name}）")
    print(
        f"{'OK 已信任' if added else '已存在'} {args.name} 指纹 {creator.key_fingerprint}"
    )
    return 0


def cmd_trust_revoke(args: argparse.Namespace) -> int:
    revoked = TrustedUserList().revoke(args.fingerprint)
    print(f"{'OK 已撤销信任' if revoked else '未找到'} {args.fingerprint}")
    return 0 if revoked else 1


def cmd_trust_list(args: argparse.Namespace) -> int:
    users = TrustedUserList().list_users()
    if not users:
        print("信任列表为空。")
        return 0
    print(f"信任列表（{len(users)}）:")
    for user in users:
        print(f"  {user.username}  {user.key_fingerprint}  信任于 {user.trusted_at}（{user.source}）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="identity", description="本地身份与信任列表 CLI")
    parser.add_argument(
        "--password", default=None, help="身份密码（默认读环境变量 OMNICRAWL_IDENTITY_PASSWORD）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="创建本地身份")
    create.add_argument("username", help="用户名（^[a-z0-9_-]{2,32}$，本地唯一）")

    sub.add_parser("list", help="列出本地身份")
    show = sub.add_parser("show", help="显示身份详情")
    show.add_argument("username", help="用户名")
    delete = sub.add_parser("delete", help="注销身份（需密码）")
    delete.add_argument("username", help="用户名")

    trust = sub.add_parser("trust", help="信任列表管理")
    trust_sub = trust.add_subparsers(dest="trust_command", required=True)
    add = trust_sub.add_parser("add", help="信任某创作者（需其 ed25519 公钥 PEM 文件）")
    add.add_argument("--pubkey", required=True, help="创作者公钥 PEM 文件路径（或内联 PEM 文本）")
    add.add_argument("--name", required=True, help="创作者显示名")
    revoke = trust_sub.add_parser("revoke", help="撤销信任")
    revoke.add_argument("fingerprint", help="公钥指纹")
    trust_sub.add_parser("list", help="列出信任列表")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "trust":
        handlers = {
            "add": cmd_trust_add,
            "revoke": cmd_trust_revoke,
            "list": cmd_trust_list,
        }
        return handlers[args.trust_command](args)
    handlers = {
        "create": cmd_create,
        "list": cmd_list,
        "show": cmd_show,
        "delete": cmd_delete,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
