#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""市场重签工具：用新私钥重签全部插件/模板的签名三件套。

本市场存在两套**独立**的签名身份，各自独立验签（详见 plugin.yaml 注释）：

  1. 分发签名轨  <target>.sig（即 plugin.py.sig / template.yaml.sig）
     用【维护者冷私钥】对 plugin.py / template.yaml 原文签名。
     客户端加载时用信任根公钥（keys/plugin_trust.pub.pem，
     authors/starlife-creator.yaml，指纹 d92fa9fb…）验签。
     → 换了维护者冷私钥，必须重签这一轨（并同步信任根与 authors 记录）。

  2. 创作者轨  creator.sig + creator.identity
     creator.identity 是作者公开身份（username + 公钥 + 指纹）；
     creator.sig 用作者私钥（本地 IdentityStore，密码保护，作者 starlife，
     指纹 4c3014…）对 plugin.py / template.yaml 签名。
     → 重建/换了作者身份，必须重签这一轨（并同步 keys/、authors/、
       plugins/*/plugin.yaml 的 author_fingerprint）。

用法：
  # 场景 A：仅换维护者冷私钥（作者身份不变，只重签 *.sig + 同步信任根）
  python tools/ressign_market.py --maintainer-key /cold/path/plugin_signing_private.pem

  # 场景 A + 同步主仓库信任根（configs/plugin_trust.pub.pem）
  python tools/ressign_market.py --maintainer-key /cold/path/plugin_signing_private.pem \\
      --sync-main-repo

  # 场景 B：作者身份也重建/更换（连 creator.sig + creator.identity 一起重签）
  OMNICRAWL_IDENTITY_PASSWORD='作者密码' \\
      python tools/ressign_market.py --maintainer-key /cold/path/plugin_signing_private.pem \\
      --author starlife

  # 预览不写盘
  python tools/ressign_market.py --maintainer-key ... --dry-run

主仓同步（默认开启）：
  重签完成后自动把每个插件的签名产物集（plugin.py + plugin.py.sig，及存在的
  creator.sig + creator.identity）同步到 OmniCrawler/plugins_installed/<name>/，
  并用主仓信任根复验 installed 副本——保证"市场是唯一签名权威源、installed
  副本与签名同源"。目标目录不存在时自动创建；installed 目录内的额外文件
  （如 listing.md）不受影响。--no-sync-installed 可关闭。

安全约束：
  - 私钥只从 --maintainer-key / IdentityStore 读取，绝不写入工作区；
  - 透明日志只记录公开元数据（时间 / 文件哈希 / 操作者 / 目标文件名）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# -- 自引导：让工具在直接用 `python tools/ressign_market.py` 时也能找到 omnicrawler --
# FINAL-M5：本脚本收编入主仓 tools/（原游离于工作区根、不受版本控制/评审）。
# 双仓库布局（README）：主仓与市场仓必须放在同一父目录下，故——
#   主仓根 = 本脚本目录（tools/）的父目录；市场仓 = 主仓根的兄弟目录。
_SCRIPT_DIR = Path(__file__).resolve().parent
_MAIN_REPO = _SCRIPT_DIR.parent
_REGISTRY = _MAIN_REPO.parent / "OmniCrawler-market"
for _cand in (_MAIN_REPO / "src", _MAIN_REPO):
    if str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

from omnicrawler.plugins.identity import derive_fingerprint  # noqa: E402
from omnicrawler.plugins.signing import sign_file, verify_plugin  # noqa: E402

TRUST_PUB = _REGISTRY / "keys" / "plugin_trust.pub.pem"
MAIN_REPO_TRUST = _MAIN_REPO / "configs" / "plugin_trust.pub.pem"
TRANSPARENCY_LOG = _REGISTRY / "signing_transparency.jsonl"
AUTHOR_RECORD_CREATOR = _REGISTRY / "authors" / "starlife.yaml"
AUTHOR_RECORD_MAINTAINER = _REGISTRY / "authors" / "starlife-creator.yaml"
OP_MAINTAINER = "starlife-creator"
OP_AUTHOR = "starlife"

# 目标文件模式：插件轨与模板轨
_ENTRY_GLOBS = [
    ("plugins/*/plugin.py", "plugins"),
    ("templates/*/template.yaml", "templates"),
]


def _discover(registry: Path) -> list[Path]:
    found: list[Path] = []
    for pattern, _ in _ENTRY_GLOBS:
        found.extend(sorted(registry.glob(pattern)))
    return found


def _append_log(target: Path, operator: str) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "plugin": target.as_posix(),
        "plugin_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "operator": operator,
        "operation": "sign",
        "note": "签名者私钥未导出；本日志仅记录公开元数据",
    }
    with TRANSPARENCY_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"    [透明日志] 已追加: {TRANSPARENCY_LOG.name}")


def _public_pem_from_private(private_pem: bytes) -> bytes:
    """从私钥 PEM 导出 SubjectPublicKeyInfo PEM（更新信任根用）。"""
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(private_pem, password=None)
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _author_public_pem(username: str, password: str) -> tuple[bytes, str]:
    """返回 (作者公钥 SubjectPublicKeyInfo PEM, 指纹)，从本地身份加载。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from omnicrawler.plugins.identity import IdentityStore

    identity = IdentityStore().load(username, password)
    raw = identity.public_key_bytes
    pem = Ed25519PublicKey.from_public_bytes(raw).public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pem, derive_fingerprint(raw)


def _set_yaml_field(path: Path, field: str, new_value: str) -> None:
    """正则原地替换 YAML 顶层字段值，保留注释（authors/*.yaml 注释有语义）。"""
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        rf"(?m)^({field}:\s*).*$", rf"\g<1>{new_value}", text,
    )
    if n == 0:
        raise RuntimeError(f"未找到字段 {field} in {path}")
    path.write_text(new_text, encoding="utf-8")
    print(f"    已更新 {path.name}: {field} = {new_value}")


def _compute_maintainer_fp(private_pem: bytes) -> str:
    """只计算维护者冷私钥对应公钥的指纹（不写盘）。"""
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(private_pem, password=None)
    return derive_fingerprint(key.public_key().public_bytes_raw())


def _sync_maintainer_trust(private_pem: bytes, sync_main_repo: bool) -> None:
    """同步维护者信任根：keys/plugin_trust.pub.pem + authors/starlife-creator.yaml。"""
    fp = _compute_maintainer_fp(private_pem)
    new_pub = _public_pem_from_private(private_pem)
    TRUST_PUB.write_bytes(new_pub)
    print(f"[信任根] 已更新 {TRUST_PUB.relative_to(_REGISTRY)}")
    _set_yaml_field(AUTHOR_RECORD_MAINTAINER, "fingerprint", fp)
    if sync_main_repo and MAIN_REPO_TRUST.is_file():
        MAIN_REPO_TRUST.write_bytes(new_pub)
        print(f"[信任根] 已同步主仓库 {MAIN_REPO_TRUST.relative_to(_MAIN_REPO)}")


def _sync_author(old_fp: str, new_fp: str, author_pem: bytes, author_name: str) -> None:
    """作者换钥时同步：keys/<新指纹>.pub.pem、authors/starlife.yaml、各 plugin.yaml。"""
    keys_dir = _REGISTRY / "keys"
    (keys_dir / f"{new_fp}.pub.pem").write_bytes(author_pem)
    print(f"[作者换钥] 已写入 {keys_dir.name}/{new_fp}.pub.pem")
    # authors/starlife.yaml：fingerprint + pubkey_ref
    _set_yaml_field(AUTHOR_RECORD_CREATOR, "fingerprint", new_fp)
    _set_yaml_field(AUTHOR_RECORD_CREATOR, "pubkey_ref", f"../keys/{new_fp}.pub.pem")
    # plugins/*/plugin.yaml 的 author_fingerprint（老指纹 → 新指纹）
    for manifest in _REGISTRY.glob("plugins/*/plugin.yaml"):
        text = manifest.read_text(encoding="utf-8")
        new_text, n = re.subn(
            rf"(?m)^(author_fingerprint:\s*){re.escape(old_fp)}\b",
            rf"\g<1>{new_fp}",
            text,
        )
        if n:
            manifest.write_text(new_text, encoding="utf-8")
            print(f"    已更新 {manifest.relative_to(_REGISTRY).as_posix()}: author_fingerprint -> {new_fp}")
    old_key = keys_dir / f"{old_fp}.pub.pem"
    if old_key.is_file():
        print(
            f"[作者换钥] 旧公钥 {old_key.name} 已不再被引用，"
            "请确认无其他引用后手动删除（脚本不代删）。"
        )


def _regenerate_catalog() -> int:
    gen = _REGISTRY / "tools" / "generate_catalog.py"
    if not gen.is_file():
        print("    [跳过] 未找到 generate_catalog.py，catalog.json 未重新生成")
        return 0
    result = subprocess.run(
        [sys.executable, str(gen), "--registry", str(_REGISTRY)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0 and result.stderr:
        print(result.stderr, end="")
    return result.returncode


def _sync_installed(targets: list[Path]) -> int:
    """把市场插件的签名产物集同步到主仓 plugins_installed/ 并复验（返回失败数）。

    市场是唯一签名权威源：重签后 installed 副本必须与签名同源，否则主仓
    加载器会因 plugin.py 与 plugin.py.sig 哈希不一致而验签失败（2026-08-21
    实测踩坑）。只同步插件轨（templates 无 installed 副本）。
    """
    installed_root = _MAIN_REPO / "plugins_installed"
    # 复验信任根：主仓加载器用 configs/plugin_trust.pub.pem（与 --sync-main-repo 联动）
    trust = MAIN_REPO_TRUST if MAIN_REPO_TRUST.is_file() else TRUST_PUB
    failures = 0
    synced = 0
    for target in targets:
        if "plugins" not in target.parts:
            continue
        name = target.parent.name
        dest = installed_root / name
        dest.mkdir(parents=True, exist_ok=True)
        for fname in (target.name, target.name + ".sig", "creator.sig", "creator.identity"):
            src = target.parent / fname
            if not src.is_file():
                continue
            # 内容相同则跳过，保持 mtime 稳定（避免触发不必要的 git 变更）
            dst = dest / fname
            if dst.is_file() and dst.read_bytes() == src.read_bytes():
                continue
            shutil.copyfile(src, dst)
            synced += 1
        # 复验 installed 副本的分发轨（防同步出错）
        vok, reason = verify_plugin(str(dest / target.name), str(trust))
        print(f"    {'OK ' if vok else 'FAIL'} installed 副本 {name}: {reason}")
        if not vok:
            failures += 1
    if synced or targets:
        print(f"[installed] 已同步 {synced} 个文件到 {installed_root.relative_to(_MAIN_REPO)}/（复验信任根: {trust.name}）")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="重签市场插件/模板签名（分发轨用维护者冷私钥，创作者轨用作者身份）"
    )
    parser.add_argument("--maintainer-key", required=True, help="维护者冷私钥 PEM 路径（绝不入仓）")
    parser.add_argument(
        "--author", default=None, help="作者用户名（提供则重签创作者轨 creator.sig+creator.identity）"
    )
    parser.add_argument(
        "--author-password", default=None,
        help="作者身份密码（默认读环境变量 OMNICRAWL_IDENTITY_PASSWORD）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只预览要重签的文件，不写盘")
    parser.add_argument(
        "--sync-main-repo", action="store_true",
        help="同步更新主仓库 configs/plugin_trust.pub.pem（场景A换信任根时用）",
    )
    parser.add_argument("--skip-catalog", action="store_true", help="不重新生成 catalog.json")
    parser.add_argument(
        "--no-sync-installed", action="store_true",
        help="不同步签名产物到主仓 OmniCrawler/plugins_installed/（默认自动同步）",
    )
    args = parser.parse_args(argv)

    private_path = Path(args.maintainer_key).expanduser()
    if not private_path.is_file():
        print(f"[FAIL] 维护者冷私钥不存在: {private_path}")
        return 2
    private_pem = private_path.read_bytes()
    try:
        new_maintainer_fp = _compute_maintainer_fp(private_pem)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 私钥无法解析（必须是 ed25519 PKCS8 PEM）: {exc}")
        return 2
    print(f"[info] 新信任根公钥指纹: {new_maintainer_fp}")

    author_password: str | None = None
    if args.author:
        author_password = args.author_password or os.environ.get(
            "OMNICRAWL_IDENTITY_PASSWORD", ""
        )
        if not author_password:
            print(
                "[FAIL] 提供 --author 时必须同时提供 --author-password 或设置 "
                "环境变量 OMNICRAWL_IDENTITY_PASSWORD"
            )
            return 2

    # 作者换钥检测：重签前后 creator.identity 指纹对比
    old_author_fp: str | None = None
    new_author_pem: bytes | None = None
    new_author_fp: str | None = None
    if args.author:
        first = _discover(_REGISTRY)[0]
        old_identity = first.parent / "creator.identity"
        if old_identity.is_file():
            try:
                old_author_fp = str(
                    json.loads(old_identity.read_text(encoding="utf-8")).get("key_fingerprint", "")
                )
            except json.JSONDecodeError:
                old_author_fp = None
        new_author_pem, new_author_fp = _author_public_pem(args.author, author_password)
        print(f"[info] 作者 {args.author} 指纹: {new_author_fp}（旧: {old_author_fp}）")

    targets = _discover(_REGISTRY)
    if not targets:
        print("[FAIL] 未发现任何插件/模板目标文件")
        return 2
    print(f"[info] 发现 {len(targets)} 个目标文件，私钥: {private_path}")

    ok = failed = 0
    for target in targets:
        sig_path = target.with_suffix(target.suffix + ".sig")
        print(f"\n== {target.relative_to(_REGISTRY).as_posix()}")
        if args.dry_run:
            print(f"    [dry-run] 将重签分发轨: {sig_path.name}")
            if args.author:
                print(f"    [dry-run] 将重签创作者轨: creator.sig + creator.identity（{args.author}）")
            ok += 1
            continue
        # 1) 分发轨：维护者冷私钥
        try:
            sign_file(target, private_pem)
            print(f"    [分发轨] 已重签: {sig_path.name}（维护者冷密钥）")
            _append_log(target, OP_MAINTAINER)
        except Exception as exc:  # noqa: BLE001
            print(f"    [FAIL] 分发轨重签失败: {exc}")
            failed += 1
            continue
        # 2) 创作者轨：作者身份（可选）
        if args.author:
            try:
                from omnicrawler.plugins.identity import IdentityStore

                identity = IdentityStore().load(args.author, author_password)
                creator = identity.export_identity()
                sig = identity.sign_bytes(target.read_bytes())
                (target.parent / "creator.sig").write_bytes(sig)
                (target.parent / "creator.identity").write_text(
                    json.dumps(creator.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(
                    f"    [创作者轨] 已重签: creator.sig + creator.identity"
                    f"（{args.author}，指纹 {creator.key_fingerprint}）"
                )
                _append_log(target, OP_AUTHOR)
            except Exception as exc:  # noqa: BLE001
                print(f"    [FAIL] 创作者轨重签失败: {exc}")
                failed += 1
        ok += 1

    if args.dry_run:
        print(f"\n[dry-run] 预览完成: {ok} 个目标（未写盘）")
        return 0

    # 3) 维护者信任根同步（dry-run 已跳过；换钥才实际落盘）
    _sync_maintainer_trust(private_pem, args.sync_main_repo)

    # 4) 作者换钥同步（仅在作者身份指纹确实变化时执行）
    if args.author and old_author_fp and new_author_fp and old_author_fp != new_author_fp:
        _sync_author(old_author_fp, new_author_fp, new_author_pem, args.author)

    # 5) 重新生成 catalog.json（失败计入错误）
    if not args.skip_catalog:
        print("\n[catalog] 重新生成 catalog.json …")
        if _regenerate_catalog() != 0:
            print("[FAIL] catalog.json 生成失败")
            failed += 1

    # 6) 验签（分发轨）
    print("\n[验签] 用信任根公钥校验分发轨:")
    for target in targets:
        vok, reason = verify_plugin(str(target), str(TRUST_PUB))
        print(f"    {'OK ' if vok else 'FAIL'} {target.relative_to(_REGISTRY).as_posix()}: {reason}")
        if not vok:
            failed += 1

    # 7) 同步签名产物到主仓 plugins_installed/ 并复验（--no-sync-installed 关闭）
    if not args.no_sync_installed:
        print("\n[installed] 同步到主仓 plugins_installed/ …")
        failed += _sync_installed(targets)

    print(f"\n[done] 成功 {ok}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
