#!/usr/bin/env python
"""Offline plugin signing tool — run ONLY on the cold signing host.

This tool is intentionally NOT part of the runtime/portable build. It is used
by the operator on an isolated machine to:

- ``generate-keys`` : create an ed25519 keypair. The PRIVATE key is written to
  the operator-specified cold-storage location and must be moved away
  immediately; the PUBLIC key is safe to ship (it becomes the trust root).
- ``scan``          : run the pre-publish security scan (five checks) on a
  plugin directory before signing.
- ``sign``          : produce a detached ``<plugin>.sig`` for a plugin file.
  Runs the security scan first (unless ``--skip-scan``) and appends a
  transparency-log entry after signing.
- ``verify``        : check a plugin's detached signature against a trust root.

Cold-key principle: the private key is generated at a location the operator can
immediately cut to cold storage; it must never enter the repo, build outputs,
or the portable zip.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# -- 自引导：让工具在直接用 `python tools/sign_plugin.py` 时也能找到 omnicrawl --
# 即便没有可编辑安装（如全新 checkout），只要运行它的 Python 装了 cryptography 即可。
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _cand in (_REPO_ROOT / "src", _REPO_ROOT):
    _cand_str = str(_cand)
    if _cand_str not in sys.path:
        sys.path.insert(0, _cand_str)

from omnicrawl.plugins.signing import (  # noqa: E402
    generate_keypair,
    sign_file,
    verify_plugin,
)


# Designated private-key generation location (operator moves it to cold storage
# immediately after generation). Override with --private-out.
def _default_private_path() -> str:
    """用户主目录下的 .omnicrawl/keys（无 HOME 时回退仓库内 .private_keys/）。"""
    try:
        return str(Path.home() / ".omnicrawl" / "keys" / "plugin_signing_private.pem")
    except RuntimeError:
        return str(_REPO_ROOT / ".private_keys" / "plugin_signing_private.pem")


PRIVATE_DEFAULT = _default_private_path()
PUBLIC_DEFAULT = "configs/plugin_trust.pub.pem"
SCANNER = _REPO_ROOT.parent / "OmniCrawler-market" / "tools" / "scan_plugin.py"
TRANSPARENCY_LOG_DEFAULT = "signing_transparency.jsonl"


def _generate_keys(private_out: Path, public_out: Path) -> None:
    private_pem, public_pem = generate_keypair()
    private_out.parent.mkdir(parents=True, exist_ok=True)
    private_out.write_bytes(private_pem)
    # 冷私钥权限收紧：ed25519 私钥明文落盘必须 0600，禁止继承默认 umask（0644）
    try:
        os.chmod(private_out, 0o600)
    except OSError:  # Windows 上 chmod 语义受限，尽力而为
        pass
    public_out.parent.mkdir(parents=True, exist_ok=True)
    public_out.write_bytes(public_pem)
    print(f"[冷密钥] 私钥已写入: {private_out}（权限 0600）")
    print("[冷密钥] 请立即将其剪切至加密 U 盘/离线机，并安全擦除源位置（覆写、不走回收站）。")
    print(f"[信任根] 公钥已写入（可随构建分发）: {public_out}")


def _run_scan(plugin: Path, manifest: Path | None) -> None:
    """发布前**凭据泄漏检查**（scan_plugin.py：敏感文件/高熵串/API Token/私钥字段）。

    **不是代码行为安全扫描**——scan_plugin.py 不分析恶意代码，危险调用检查
    由客户端加载期的 AST 预检（B2）承担，两者是不同的事（审查报告 S48）。
    文案从"第二道防线"改为"凭据泄漏检查"，避免维护者在错误的安全预期上
    做决策。

    **fail-closed（S41①）**：找不到扫描器时**中止签名**，而不是打印一行
    "跳过"继续——docstring 承诺的"五项发布前安全检查"对只克隆主仓的操作者
    一次都没跑却照常签名，等于门禁在 public 仓场景下是空的。
    显式 ``--skip-scan`` 仍是唯一逃生口（不推荐）。
    """
    if not SCANNER.is_file():
        sys.exit(
            f"[扫描] FAIL：未找到发布前扫描器 {SCANNER}\n"
            "签名已中止。请先克隆 OmniCrawler-market 仓库（扫描器在 "
            "OmniCrawler-market/tools/scan_plugin.py），或显式使用 --skip-scan 跳过（不推荐）。"
        )
    cmd = [sys.executable, str(SCANNER), "scan", str(plugin)]
    if manifest and manifest.is_file():
        cmd += ["--manifest", str(manifest)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode != 0:
        sys.exit("发布前扫描发现问题，中止签名（--skip-scan 可跳过，不推荐）")


def _run_scan_cli(plugin_dir: Path, manifest: Path | None) -> int:
    """``scan`` 子命令：直接驱动生态扫描器。"""
    if not SCANNER.is_file():
        print(f"未找到扫描器 {SCANNER}")
        return 1
    cmd = [sys.executable, str(SCANNER), "scan", str(plugin_dir)]
    if manifest and manifest.is_file():
        cmd += ["--manifest", str(manifest)]
    return subprocess.run(cmd).returncode


def _current_operator(override: str | None = None) -> str:
    """当前操作者（显式参数 > 环境变量 > getpass 兜底，失败返回 unknown）。

    B02-004：操作者身份必须可显式配置（审计归属），不再仅靠机器用户名推断。
    """
    if override:
        return override
    for var in ("USERNAME", "USER", "LOGNAME"):
        value = os.environ.get(var)
        if value:
            return value
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - Windows 无 pwd 模块或环境缺用户名时兜底
        return "unknown"


def _append_transparency_log(
    plugin: Path, log_path: Path, operator: str | None = None,
) -> None:
    """签名透明日志：时间 / 文件哈希 / 操作者 / 摘要（仅记录公开信息）。

    B02-004：写日志是冷密钥签名的必经步骤；日志写失败时异常向上传播，
    使签名整体失败（fail-closed），确保每次冷密钥动用都留下公开记录。
    """
    digest = hashlib.sha256(plugin.read_bytes()).hexdigest()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plugin": plugin.as_posix(),  # B02-004：正斜杠，跨平台稳定
        "plugin_sha256": digest,
        "operator": _current_operator(operator),
        "operation": "sign",
        "note": "签名者私钥未导出；本日志仅记录公开元数据",
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[透明日志] 已追加: {log_path}")


def _resolve_password(password: str | None) -> str:
    if password:
        return password
    value = os.environ.get("OMNICRAWL_IDENTITY_PASSWORD", "")
    if value:
        return value
    raise ValueError("需要身份密码：--password 或环境变量 OMNICRAWL_IDENTITY_PASSWORD")


def _creator_sign(plugin_dir: Path, username: str, password: str, target: str) -> Path:
    """创建即签名：用本地身份生成 creator.sig + creator.identity（三件套之二）。"""
    from omnicrawl.plugins.identity import IdentityStore

    target_path = plugin_dir / target
    if not target_path.is_file():
        raise FileNotFoundError(f"缺少待签名文件 {target}: {target_path}")
    identity = IdentityStore().load(username, password)
    creator = identity.export_identity()
    signature = identity.sign_bytes(target_path.read_bytes())
    (plugin_dir / "creator.sig").write_bytes(signature)
    (plugin_dir / "creator.identity").write_text(
        json.dumps(creator.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"[创作者签名] 已生成 creator.sig + creator.identity（作者: {username}，"
        f"指纹: {creator.key_fingerprint}，目标: {target}）"
    )
    return plugin_dir / "creator.identity"


def _local_sign(plugin_dir: Path, username: str, password: str, target: str) -> Path:
    """本地一键签名：creator-sign + 自动加入本地信任列表。

    解决"签名了但指纹未入信任列表反而被拒载"的坑——本地用户给
    自己签名后，本机加载立即可用且显示作者。
    """
    identity_path = _creator_sign(plugin_dir, username, password, target)
    from omnicrawl.plugins.identity import CreatorIdentity
    from omnicrawl.plugins.trust import TrustedUserList

    creator = CreatorIdentity.from_dict(json.loads(identity_path.read_text(encoding="utf-8")))
    if TrustedUserList().add(creator, source="local", path_hint=f"（{plugin_dir}）"):
        print(f"[本地信任] 已加入信任列表: {username}（指纹 {creator.key_fingerprint}）")
    else:
        print(f"[本地信任] 作者 {username} 已在信任列表")
    return identity_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线插件签名工具（仅冷机器运行）")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-keys", help="生成 ed25519 密钥对")
    gen.add_argument("--private-out", default=PRIVATE_DEFAULT, help="私钥输出路径（冷存储）")
    gen.add_argument("--public-out", default=PUBLIC_DEFAULT, help="公钥输出路径（信任根）")

    sc = sub.add_parser("scan", help="发布前安全扫描插件目录")
    sc.add_argument("plugin_dir", help="插件目录路径")
    sc.add_argument("--manifest", default=None, help="插件清单 YAML 路径（允许列表校验）")

    sg = sub.add_parser("sign", help="对插件文件签名（生成 <plugin>.sig）")
    sg.add_argument("plugin", help="插件 .py 文件路径")
    sg.add_argument("--private-key", default=PRIVATE_DEFAULT, help="签名私钥 PEM 路径")
    sg.add_argument("--manifest", default=None, help="插件清单 YAML 路径（扫描允许列表）")
    sg.add_argument("--skip-scan", action="store_true", help="跳过发布前扫描（不推荐）")
    sg.add_argument("--log", default=TRANSPARENCY_LOG_DEFAULT, help="签名透明日志路径")
    sg.add_argument(
        "--operator", default=None,
        help="操作者标识（审计归属，B02-004；缺省回退环境变量/系统用户名）",
    )

    cs = sub.add_parser(
        "creator-sign",
        help="创作者签名（生成 creator.sig + creator.identity，创建即签名）",
    )
    cs.add_argument("plugin_dir", help="插件/模板目录路径")
    cs.add_argument(
        "--file", default="plugin.py", help="待签名文件名（默认 plugin.py；模板用 template.yaml）"
    )
    cs.add_argument("--username", required=True, help="本地身份用户名")
    cs.add_argument("--password", default=None, help="身份密码（默认读环境变量 OMNICRAWL_IDENTITY_PASSWORD）")
    cs.add_argument("--manifest", default=None, help="插件清单 YAML 路径（扫描允许列表）")
    cs.add_argument("--skip-scan", action="store_true", help="跳过发布前扫描（不推荐）")

    ls = sub.add_parser(
        "local-sign",
        help="本地一键签名：creator-sign + 自动加入本地信任列表（本机立即可加载并显示作者）",
    )
    ls.add_argument("plugin_dir", help="插件/模板目录路径")
    ls.add_argument(
        "--file", default="plugin.py", help="待签名文件名（默认 plugin.py；模板用 template.yaml）"
    )
    ls.add_argument("--username", required=True, help="本地身份用户名")
    ls.add_argument("--password", default=None, help="身份密码（默认读环境变量 OMNICRAWL_IDENTITY_PASSWORD）")
    ls.add_argument("--manifest", default=None, help="插件清单 YAML 路径（扫描允许列表）")
    ls.add_argument("--skip-scan", action="store_true", help="跳过发布前扫描（不推荐）")

    vf = sub.add_parser("verify", help="验签插件")
    vf.add_argument("plugin", help="插件 .py 文件路径")
    vf.add_argument("--trust", required=True, help="信任根公钥 PEM 或公钥文件路径")

    args = parser.parse_args(argv)

    if args.command == "generate-keys":
        _generate_keys(Path(args.private_out).expanduser(), Path(args.public_out).expanduser())
        return 0
    if args.command == "scan":
        manifest = Path(args.manifest) if args.manifest else None
        return _run_scan_cli(Path(args.plugin_dir), manifest)
    if args.command == "sign":
        plugin_path = Path(args.plugin)
        if not args.skip_scan:
            _run_scan(plugin_path.parent, Path(args.manifest) if args.manifest else None)
        private_pem = Path(args.private_key).expanduser().read_bytes()
        sig = sign_file(args.plugin, private_pem)
        print(f"已签名: {sig}")
        _append_transparency_log(plugin_path, Path(args.log), operator=args.operator)
        return 0
    if args.command == "creator-sign":
        plugin_dir = Path(args.plugin_dir)
        if not args.skip_scan:
            _run_scan(plugin_dir, Path(args.manifest) if args.manifest else None)
        try:
            _creator_sign(plugin_dir, args.username, _resolve_password(args.password), args.file)
        except Exception as exc:  # noqa: BLE001 - 统一给出可读错误
            print(f"FAIL 创作者签名: {exc}")
            return 1
        return 0
    if args.command == "local-sign":
        plugin_dir = Path(args.plugin_dir)
        if not args.skip_scan:
            _run_scan(plugin_dir, Path(args.manifest) if args.manifest else None)
        try:
            _local_sign(plugin_dir, args.username, _resolve_password(args.password), args.file)
        except Exception as exc:  # noqa: BLE001 - 统一给出可读错误
            print(f"FAIL 本地签名: {exc}")
            return 1
        return 0
    if args.command == "verify":
        ok, reason = verify_plugin(args.plugin, args.trust)
        print(("OK: " if ok else "FAIL: ") + reason)
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
