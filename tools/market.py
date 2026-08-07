#!/usr/bin/env python3
"""OmniCrawler 策展式插件市场 CLI。

子命令：
  list               列出 catalog 中已审核插件
  info ID            显示某插件功能说明（listing.md）
  install ID         下载并验签安装插件到本地
  verify ID          重新验签已安装插件

catalog_url 默认取自配置 ``plugins.catalog_url``（主仓库 raw），
可经 ``--catalog-url`` 覆盖——指向镜像或独立仓库即完成迁移，目录内部路径不变。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 自引导：允许从仓库根直接运行（裸 python 也能找到 omnicrawl 包）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from omnicrawl.plugins.market_client import (  # noqa: E402
    download_and_verify,
    fetch_catalog,
    fetch_resource,
    verify_installed,
)

DEFAULT_CATALOG_URL = "https://raw.githubusercontent.com/Starlife-creator/omnicrawler/main/registry"
DEFAULT_DEST = ROOT / "plugins_installed"


def _resolve_catalog_url(arg: str | None) -> str:
    if arg:
        return arg
    try:
        from omnicrawl.core.config import DEFAULTS

        url = DEFAULTS["plugins"]["catalog_url"]
        if url:
            return url
    except Exception:
        pass
    return DEFAULT_CATALOG_URL


def _resolve_trust(arg: str | None) -> str:
    if arg:
        return arg
    fallback = ROOT / "configs" / "plugin_trust.pub.pem"
    if fallback.is_file():
        return str(fallback)
    sys.exit("错误：未配置信任根公钥，无法验签。请在 plugins.trust_public_key 配置 ed25519 公钥。")


def cmd_list(args: argparse.Namespace) -> int:
    catalog = fetch_catalog(_resolve_catalog_url(args.catalog_url))
    entries = catalog.get("plugins", [])
    if not entries:
        print("catalog 中暂无插件。")
        return 0
    print(f"已审核插件 ({len(entries)}) — 发布者: {catalog.get('publisher', '未知')}")
    print("-" * 64)
    for entry in entries:
        perms = ",".join(entry.get("permissions", [])) or "无"
        print(f"  {entry['id']}  v{entry['version']}  [{entry.get('category', '')}]")
        print(f"     {entry.get('summary', '')}")
        print(
            f"     权限: {perms}  兼容核心: {entry.get('compatible_core', '')}  "
            f"许可: {entry.get('license', '')}"
        )
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    catalog_url = _resolve_catalog_url(args.catalog_url)
    catalog = fetch_catalog(catalog_url)
    entry = None
    for candidate in catalog.get("plugins", []):
        if candidate["id"] == args.id:
            entry = candidate
            break
    if entry is None:
        print(f"catalog 中无此插件: {args.id}")
        return 1
    print(f"# {entry['name']} ({entry['id']} v{entry['version']})")
    print(
        f"发布者: {entry.get('publisher')}  类别: {entry.get('category')}  "
        f"许可: {entry.get('license')}"
    )
    print(
        f"兼容核心: {entry.get('compatible_core')}  权限: "
        f"{','.join(entry.get('permissions', [])) or '无'}"
    )
    print(f"标签: {', '.join(entry.get('tags', []))}")
    print(f"摘要: {entry.get('summary')}")
    try:
        listing = fetch_resource(catalog_url, entry["description_file"]).decode("utf-8")
        print("\n--- 功能说明 (listing.md) ---\n")
        print(listing)
    except Exception as exc:  # noqa: BLE001
        print(f"\n(无法获取功能说明: {exc})")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    dest = Path(args.dest)
    trust = _resolve_trust(args.trust)
    try:
        path = download_and_verify(
            args.id, _resolve_catalog_url(args.catalog_url), dest, trust, timeout=args.timeout
        )
    except (KeyError, ValueError, PermissionError, FileNotFoundError) as exc:
        print(f"安装失败: {exc}")
        return 1
    print(f"已安装并验签: {path}")
    print(f"安装目录: {path.parent}")
    print("启用方式：将 'plugins_installed' 加入配置的 plugins.paths（GUI 市场面板将自动发现）。")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    ok, reason = verify_installed(args.dest, args.id, _resolve_trust(args.trust))
    print(f"{'OK' if ok else 'FAIL'} {args.id}: {reason}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="market", description="OmniCrawler 策展式插件市场 CLI")
    parser.add_argument("--catalog-url", default=None, help="catalog 基址（默认取配置或主仓库 raw）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出已审核插件")
    info_p = sub.add_parser("info", help="显示插件功能说明")
    info_p.add_argument("id", help="插件 ID")
    inst_p = sub.add_parser("install", help="下载并验签安装插件")
    inst_p.add_argument("id", help="插件 ID")
    inst_p.add_argument("--dest", default=str(DEFAULT_DEST), help="安装根目录（默认 plugins_installed）")
    inst_p.add_argument("--trust", default=None, help="信任根公钥 PEM 或路径")
    inst_p.add_argument("--timeout", type=float, default=15.0)
    ver_p = sub.add_parser("verify", help="重新验签已安装插件")
    ver_p.add_argument("id", help="插件 ID")
    ver_p.add_argument("--dest", default=str(DEFAULT_DEST))
    ver_p.add_argument("--trust", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "list": cmd_list,
        "info": cmd_info,
        "install": cmd_install,
        "verify": cmd_verify,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
