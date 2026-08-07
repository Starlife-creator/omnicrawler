#!/usr/bin/env python
"""Batch-sign OmniCrawler plugins offline — run ONLY on the cold signing host.

Discovers plugin entry files (those defining a module-level
``register(registry)`` / ``register(registry, context)`` hook) and produces a
detached ``<plugin>.py.sig`` next to each, using the offline ed25519 private
key. The loader (``omnicrawl.plugins.plugins._verify_plugin_signature``) verifies
exactly these ``.sig`` files, so signing a plugin entry file is what makes it
pass the fail-closed gate once a trust root is configured.

Framework registrations inside ``src/omnicrawl`` and build/test junk directories
are excluded — they are first-party code, not user plugins, and are never subject
to the signature gate.

Usage:
    # sign everything under examples/plugins (default scan dir)
    python tools/sign_plugins_batch.py

    # sign specific files
    python tools/sign_plugins_batch.py --plugins a.py b.py

    # add a scan dir and verify each signature afterwards
    python tools/sign_plugins_batch.py --scan-dir myplugins --verify

Cold-key principle: the private key stays at the operator's cold-storage
location; it never enters the repo, build outputs, or the portable zip.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# -- 自引导：让工具在直接用 `python tools/...` 时也能找到 omnicrawl --
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _cand in (_REPO_ROOT / "src", _REPO_ROOT):
    _cand_str = str(_cand)
    if _cand_str not in sys.path:
        sys.path.insert(0, _cand_str)

from omnicrawl.plugins.signing import sign_file, verify_plugin

# Designated private-key location (operator cuts it to cold storage immediately
# after generation). Override with --private-key.
PRIVATE_DEFAULT = r"C:\Users\Lenovo\Desktop\档案\隐私\plugin_signing_private.pem"
# Trust-root public key shipped with the repo (used for post-sign verification).
PUBLIC_DEFAULT = "configs/plugin_trust.pub.pem"
DEFAULT_SCAN_DIRS = ["examples/plugins"]

# Directories that must never be treated as plugin sources.
_EXCLUDE_DIRS = {
    ".venv", "build", "artifacts", "dist", ".workbuddy", ".test-tmp",
    "e2e-artifacts", "node_modules", "__pycache__", ".git",
}
# Framework package: its register() functions are first-party registrations,
# not user plugins, and are not subject to the signature gate.
_FRAMEWORK_MARKER = Path("src", "omnicrawl")


def _is_plugin_entry(path: Path) -> bool:
    """True if the file defines a module-level ``register`` plugin hook."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "register":
            return True
    return False


def _discover(scan_dirs: list[Path]) -> list[Path]:
    found: list[Path] = []
    for base in scan_dirs:
        if not base.is_dir():
            print(f"[warn] 扫描目录不存在，跳过: {base}")
            continue
        for child in base.rglob("*.py"):
            parts = set(child.relative_to(base).parts)
            if parts & _EXCLUDE_DIRS:
                continue
            if _FRAMEWORK_MARKER in child.parents:
                continue
            if _is_plugin_entry(child):
                found.append(child)
    found.sort()
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量离线签名插件（仅冷机器运行）")
    parser.add_argument("--scan-dir", action="append", default=[], help="额外扫描目录（可多次）")
    parser.add_argument("--plugins", nargs="+", default=[], help="显式指定插件 .py 文件")
    parser.add_argument("--private-key", default=PRIVATE_DEFAULT, help="签名私钥 PEM 路径")
    parser.add_argument("--verify", action="store_true", help="签名后逐个验签")
    parser.add_argument("--dry-run", action="store_true", help="只列出将签名的文件，不写盘")
    parser.add_argument("--force", action="store_true", help="即使已存在 .sig 也重新签名")
    args = parser.parse_args(argv)

    private_path = Path(args.private_key).expanduser()
    if not private_path.is_file():
        print(f"[error] 私钥不存在: {private_path}")
        return 2

    scan_dirs = [Path(d).expanduser() for d in DEFAULT_SCAN_DIRS] + \
        [Path(d).expanduser() for d in args.scan_dir]
    discovered = _discover(scan_dirs)
    explicit = [Path(p).expanduser() for p in args.plugins]
    candidates = discovered + explicit
    # Deduplicate, preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)

    if not unique:
        print("[info] 未发现任何插件入口文件。")
        return 0

    trust_public = str(_REPO_ROOT / PUBLIC_DEFAULT)
    print(f"[info] 发现 {len(unique)} 个插件入口，准备签名（私钥: {private_path}）")
    private_pem = private_path.read_bytes()

    ok = 0
    failed = 0
    for path in unique:
        sig = path.with_suffix(path.suffix + ".sig")
        if sig.is_file() and not args.force and not args.dry_run:
            print(f"[skip] 已存在签名，跳过: {sig}（用 --force 覆盖）")
            continue
        if args.dry_run:
            print(f"[dry-run] 将签名: {path} -> {sig}")
            ok += 1
            continue
        try:
            written = sign_file(path, private_pem)
            print(f"[signed] {written}")
            if args.verify:
                vok, reason = verify_plugin(str(path), trust_public)
                print(("  [verify OK] " if vok else f"  [verify FAIL] {reason}") + trust_public)
                if not vok:
                    failed += 1
            ok += 1
        except Exception as exc:  # noqa: BLE001 - surface any signing error clearly
            print(f"[error] 签名失败 {path}: {exc}")
            failed += 1

    print(f"[done] 成功 {ok}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
