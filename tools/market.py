#!/usr/bin/env python3
"""OmniCrawler 策展式插件市场 CLI。

子命令：
  list               列出 catalog 中已审核插件
  info ID            显示某插件功能说明（listing.md）
  install ID         下载并验签安装插件到本地
  verify ID          重新验签已安装插件
  templates list     列出目录中的模板
  templates info ID  显示模板功能说明
  templates install ID  下载并验签安装模板到本地
  templates verify ID   重新验签已安装模板

catalog_url 默认取自配置 ``plugins.catalog_url``（主仓库 raw），
可经 ``--catalog-url`` 覆盖——指向镜像或独立仓库即完成迁移，目录内部路径不变。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 自引导：允许从仓库根直接运行（裸 python 也能找到 omnicrawl 包）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from omnicrawl.plugins.market_client import (  # noqa: E402
    download_and_verify,
    download_template_and_verify,
    fetch_catalog,
    fetch_resource,
    verify_installed,
    verify_installed_template,
)

DEFAULT_CATALOG_URL = str(ROOT.parent / "OmniCrawler-market")
DEFAULT_DEST = ROOT / "plugins_installed"
DEFAULT_TEMPLATE_DEST = ROOT / "templates_installed"


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
    print(f"发布者: {entry.get('publisher')}  类别: {entry.get('category')}  许可: {entry.get('license')}")
    print(f"兼容核心: {entry.get('compatible_core')}  权限: {','.join(entry.get('permissions', [])) or '无'}")
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


def _entries(catalog: dict, key: str) -> list[dict]:
    return catalog.get(key, [])


def cmd_templates_list(args: argparse.Namespace) -> int:
    catalog = fetch_catalog(_resolve_catalog_url(args.catalog_url))
    entries = _entries(catalog, "templates")
    if not entries:
        print("catalog 中暂无模板。")
        return 0
    print(f"市场模板（{len(entries)}）")
    print("-" * 64)
    for entry in entries:
        print(f"  {entry['id']}  v{entry['version']}  [{entry.get('category', '')}]")
        print(f"     {entry.get('summary', '')}")
        print(f"     发布者: {entry.get('publisher', '')}  兼容核心: {entry.get('compatible_core', '')}")
    return 0


def cmd_templates_info(args: argparse.Namespace) -> int:
    catalog_url = _resolve_catalog_url(args.catalog_url)
    catalog = fetch_catalog(catalog_url)
    entry = next((item for item in _entries(catalog, "templates") if item["id"] == args.id), None)
    if entry is None:
        print(f"catalog 中无此模板: {args.id}")
        return 1
    print(f"# {entry['name']} ({entry['id']} v{entry['version']})")
    print(f"发布者: {entry.get('publisher')}  类别: {entry.get('category')}  许可: {entry.get('license')}")
    print(f"兼容核心: {entry.get('compatible_core')}")
    print(f"标签: {', '.join(entry.get('tags', []))}")
    print(f"摘要: {entry.get('summary')}")
    if entry.get("description_file"):
        try:
            listing = fetch_resource(catalog_url, entry["description_file"]).decode("utf-8")
            print("\n--- 功能说明 (listing.md) ---\n")
            print(listing)
        except Exception as exc:  # noqa: BLE001
            print(f"\n(无法获取功能说明: {exc})")
    return 0


def cmd_templates_install(args: argparse.Namespace) -> int:
    dest = Path(args.dest)
    trust = _resolve_trust(args.trust)
    try:
        path = download_template_and_verify(
            args.id, _resolve_catalog_url(args.catalog_url), dest, trust, timeout=args.timeout
        )
    except (KeyError, ValueError, PermissionError, FileNotFoundError) as exc:
        print(f"安装失败: {exc}")
        return 1
    print(f"已安装并验签: {path}")
    print(f"安装目录: {path.parent}")
    print("启用方式：将 'templates_installed' 加入模板 user_dirs（GUI/CLI 将自动发现）。")
    return 0


def cmd_templates_verify(args: argparse.Namespace) -> int:
    ok, reason = verify_installed_template(args.dest, args.id, _resolve_trust(args.trust))
    print(f"{'OK' if ok else 'FAIL'} {args.id}: {reason}")
    return 0 if ok else 1


def cmd_templates_submit(args: argparse.Namespace) -> int:
    """构建模板上传包并发布到市场（G2）。

    默认 fork/clone/push 并创建 PR（需 gh 登录）；``--no-pr`` 仅把文件集写入
    ``--out-dir``（默认市场仓）以供本地备好、手动提交。
    """
    from omnicrawl.plugins.market_uploader import UploadError, create_market_pr
    from omnicrawl.plugins.plugin_packaging import build_template_upload

    tpl_dir = Path(args.template_dir)
    try:
        listing = Path(args.listing).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"无法读取 listing: {exc}")
        return 1
    password = args.password or os.environ.get("OMNICRAWL_IDENTITY_PASSWORD", "")
    try:
        files = build_template_upload(
            tpl_dir,
            username=args.username,
            password=password,
            template_id=args.id,
            name=args.name,
            version=args.version,
            category=args.category,
            summary=args.summary,
            listing=listing,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"构建上传包失败: {exc}")
        return 1

    if args.no_pr:
        out = Path(args.out_dir)
        for rel, content in files.items():
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        print(f"已本地备好上传包（未推 PR）：{out}")
        return 0

    title = args.title or f"模板提交：{args.id}"
    body = f"类型：template\nID：{args.id}\n由 {args.username} 通过 CLI 提交。"
    try:
        url = create_market_pr(files=files, title=title, body=body)
    except UploadError as exc:
        print(f"提交 PR 失败: {exc}")
        return 1
    print(f"已创建 PR：{url}")
    return 0


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

    tpl = sub.add_parser("templates", help="模板市场管理")
    tpl_sub = tpl.add_subparsers(dest="template_command", required=True)
    tpl_sub.add_parser("list", help="列出市场模板")
    t_info = tpl_sub.add_parser("info", help="显示模板功能说明")
    t_info.add_argument("id", help="模板 ID")
    t_inst = tpl_sub.add_parser("install", help="下载并验签安装模板")
    t_inst.add_argument("id", help="模板 ID")
    t_inst.add_argument(
        "--dest", default=str(DEFAULT_TEMPLATE_DEST), help="安装根目录（默认 templates_installed）"
    )
    t_inst.add_argument("--trust", default=None)
    t_inst.add_argument("--timeout", type=float, default=15.0)
    t_ver = tpl_sub.add_parser("verify", help="重新验签已安装模板")
    t_ver.add_argument("id", help="模板 ID")
    t_ver.add_argument("--dest", default=str(DEFAULT_TEMPLATE_DEST))
    t_ver.add_argument("--trust", default=None)
    t_sub = tpl_sub.add_parser("submit", help="构建并发布模板到市场（生成 PR）")
    t_sub.add_argument("--template-dir", required=True, help="本地模板目录（含 template.yaml）")
    t_sub.add_argument("--id", required=True, help="市场模板 ID")
    t_sub.add_argument("--name", required=True, help="模板显示名")
    t_sub.add_argument("--version", default="1.0.0", help="版本（默认 1.0.0）")
    t_sub.add_argument("--category", default="general", help="分类（默认 general）")
    t_sub.add_argument("--summary", required=True, help="简介/描述")
    t_sub.add_argument("--listing", required=True, help="listing.md 路径")
    t_sub.add_argument("--username", required=True, help="创作者身份用户名")
    t_sub.add_argument("--password", default=None, help="身份密码（默认读 OMNICRAWL_IDENTITY_PASSWORD）")
    t_sub.add_argument("--title", default=None, help="PR 标题")
    t_sub.add_argument("--no-pr", action="store_true", help="只本地备好上传包，不推 PR")
    t_sub.add_argument(
        "--out-dir", default=str(ROOT.parent / "OmniCrawler-market"), help="--no-pr 写入根目录（默认市场仓）"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "templates":
        handlers = {
            "list": cmd_templates_list,
            "info": cmd_templates_info,
            "install": cmd_templates_install,
            "verify": cmd_templates_verify,
            "submit": cmd_templates_submit,
        }
        return handlers[args.template_command](args)
    handlers = {
        "list": cmd_list,
        "info": cmd_info,
        "install": cmd_install,
        "verify": cmd_verify,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
