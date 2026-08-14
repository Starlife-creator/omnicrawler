#!/usr/bin/env python3
"""把在线市场仓的离线快照元数据同步进主仓 ``market/``（bundled_catalog_dir）。

市场仓（``OmniCrawler-market``）是 source of truth，频繁上新无需重发版。主仓
``market/`` 是冻结快照，出包时打入发行版，用户开箱即可离线浏览/安装市场。本脚本
把市场仓当前态拉进主仓，使离线快照与市场仓一致。

只同步**纯元数据**，不含插件/模板载荷（与离线条约一致）：
  - catalog.json
  - authors/   发布者身份
  - keys/      信任根 + 作者公钥

用法：
  python tools/sync_snapshot.py                       # 默认 ../OmniCrawler-market -> ./market
  python tools/sync_snapshot.py --market-repo <路径>  # 指定市场仓
  python tools/sync_snapshot.py --check               # 同步前先在市场仓跑 catalog --check
  python tools/sync_snapshot.py --dry-run             # 只报告差异，不写盘

幂等：重复执行结果一致；采用镜像模式（删除 dest 中源已无的文件）。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET_REPO = REPO_ROOT.parent / "OmniCrawler-market"
DEFAULT_DEST = REPO_ROOT / "market"

# 只需同步元数据：catalog + 发布者身份 + 信任根，绝不拉插件/模板载荷。
SYNC_ITEMS = ("catalog.json", "authors", "keys")


def _mirror(src: Path, dest: Path, stats: dict[str, int]) -> None:
    """把 src 目录镜像进 dest（src 有而 dest 无则拷贝，dest 多余则删除）。"""
    dest.mkdir(parents=True, exist_ok=True)
    for item in SYNC_ITEMS:
        s = src / item
        d = dest / item
        if s.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            # 删除 dest 多余项（以对应源子目录内容为准，而非顶层 src_names；
            # 否则子目录下所有文件都会被误判为「多余」而删空）
            s_names = {p.name for p in s.iterdir()} if s.is_dir() else set()
            for old in d.iterdir():
                if old.name not in s_names:
                    if old.is_dir():
                        shutil.rmtree(old)
                    else:
                        old.unlink()
                    stats["removed"] += 1
            # 拷贝/更新
            for child in s.iterdir():
                target = d / child.name
                if child.is_file():
                    if not target.exists() or child.read_bytes() != target.read_bytes():
                        shutil.copy2(child, target)
                        stats["updated"] += 1
                elif child.is_dir():
                    _mirror(child, target, stats)
        elif s.is_file():
            if not d.exists() or s.read_bytes() != d.read_bytes():
                shutil.copy2(s, d)
                stats["updated"] += 1


def main() -> int:
    ap = argparse.ArgumentParser(description="同步市场仓离线快照元数据进主仓 market/")
    ap.add_argument("--market-repo", type=Path, default=DEFAULT_MARKET_REPO)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--check", action="store_true", help="同步前先在市场仓跑 generate_catalog.py --check")
    ap.add_argument("--dry-run", action="store_true", help="只报告差异，不写盘")
    args = ap.parse_args()

    market_repo: Path = args.market_repo.resolve()
    dest: Path = args.dest.resolve()

    if not (market_repo / "catalog.json").is_file():
        print(f"[FAIL] 市场仓 catalog.json 不存在：{market_repo}", file=sys.stderr)
        return 1

    if args.check:
        gen = market_repo / "tools" / "generate_catalog.py"
        if not gen.is_file():
            print(f"[SKIP] 市场仓无 tools/generate_catalog.py，跳过 --check：{gen}", file=sys.stderr)
        else:
            rc = subprocess.run(
                [sys.executable, str(gen), "--check"], cwd=market_repo
            ).returncode
            if rc != 0:
                print("[FAIL] 市场仓 catalog --check 未通过，中止同步。", file=sys.stderr)
                return 1
            print("[OK] 市场仓 catalog --check 通过")

    if args.dry_run:
        stats = {"updated": 0, "removed": 0, "checked": 0}
        # dry-run：仅比对，不写盘
        for item in SYNC_ITEMS:
            s = market_repo / item
            d = dest / item
            if s.is_file():
                if not d.exists() or s.read_bytes() != d.read_bytes():
                    stats["updated"] += 1
            elif s.is_dir():
                for child in s.rglob("*"):
                    if child.is_file():
                        rel = child.relative_to(s)
                        t = d / rel
                        if not t.exists() or child.read_bytes() != t.read_bytes():
                            stats["updated"] += 1
        print(f"[DRY-RUN] 将更新 {stats['updated']} 项，删除 {stats['removed']} 项（未写盘）")
        return 0

    stats = {"updated": 0, "removed": 0}
    _mirror(market_repo, dest, stats)
    print(
        f"[OK] 已同步离线快照：{market_repo} -> {dest}\n"
        f"     更新 {stats['updated']} 项，删除 {stats['removed']} 项\n"
        f"     内容：catalog.json + authors/ + keys/（不含插件/模板载荷）\n"
        f"     下一步：git add market/ && git commit -m 'chore(market): 同步离线快照'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
