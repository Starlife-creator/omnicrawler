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

钉值联动（2026-09-25 事故固化）：快照与 ``constraints/market-ref.txt``（CI 跨仓守卫
`checkout_market.py` 按它检出市场仓）是**同一逻辑变更的两半**——同步成功后本脚本自动把
钉值跟升到市场仓 HEAD；市场仓工作区不干净（存在未提交改动）时拒绝同步（fail-closed：
钉值无法与未提交内容同源，快照 0.4.0 + 钉值 0.3.0 会让跨仓守卫必红）。

幂等：重复执行结果一致；采用镜像模式（删除 dest 中源已无的文件）。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET_REPO = REPO_ROOT.parent / "OmniCrawler-market"


def _content_equal(left: bytes, right: bytes) -> bool:
    """B02-008：换行归一化内容比对。

    Windows autocrlf 会把 git 检出的 LF 转成 CRLF，导致「全新检出快照 vs 市场仓
    LF 源」全部误报漂移。签名/指纹相关文件必须内容级比对，因此按统一 LF 归一化
    后比较字节，而不是降级为 mtime/size。
    """
    return left.replace(b"\r\n", b"\n") == right.replace(b"\r\n", b"\n")
DEFAULT_DEST = REPO_ROOT / "market"
DEFAULT_REF_FILE = REPO_ROOT / "constraints" / "market-ref.txt"


def _git_out(args: list[str], cwd: Path) -> str | None:
    """运行 git 只读命令；不可用（无 git/非仓库/超时）返回 None，调用方降级处理。"""
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def _market_state(market_repo: Path) -> tuple[str | None, bool | None]:
    """返回 (市场仓 HEAD 完整 SHA, 是否有未提交改动)；无法判定时对应项为 None。"""
    head = _git_out(["rev-parse", "HEAD"], market_repo)
    if head is None or not re.fullmatch(r"[0-9a-f]{40}", head):
        return None, None
    status = _git_out(["status", "--porcelain"], market_repo)
    if status is None:
        return head, None
    return head, bool(status)


def _bump_market_pin(ref_file: Path, head: str) -> None:
    """把钉值文件跟升到 head；已一致则不重写（幂等）。"""
    current = ref_file.read_text(encoding="utf-8").strip()
    if current == head:
        print(f"[OK] 市场钉值未变化：{head}")
        return
    ref_file.write_text(head + "\n", encoding="utf-8")
    print(f"[OK] 已更新市场钉值 {ref_file}: {current[:12]}… -> {head}")

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
                    if not target.exists() or not _content_equal(child.read_bytes(), target.read_bytes()):
                        shutil.copy2(child, target)
                        stats["updated"] += 1
                elif child.is_dir():
                    _mirror(child, target, stats)
        elif s.is_file():
            if not d.exists() or not _content_equal(s.read_bytes(), d.read_bytes()):
                shutil.copy2(s, d)
                stats["updated"] += 1


def main() -> int:
    ap = argparse.ArgumentParser(description="同步市场仓离线快照元数据进主仓 market/")
    ap.add_argument("--market-repo", type=Path, default=DEFAULT_MARKET_REPO)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--check", action="store_true", help="同步前先在市场仓跑 generate_catalog.py --check")
    ap.add_argument("--dry-run", action="store_true", help="只报告差异，不写盘")
    ap.add_argument("--ref-file", type=Path, default=DEFAULT_REF_FILE,
                    help="市场钉值文件（CI 跨仓守卫按它检出市场仓；同步成功后自动跟升）")
    args = ap.parse_args()

    market_repo: Path = args.market_repo.resolve()
    dest: Path = args.dest.resolve()
    ref_file: Path = args.ref_file.resolve()

    if not (market_repo / "catalog.json").is_file():
        print(f"[FAIL] 市场仓 catalog.json 不存在：{market_repo}", file=sys.stderr)
        return 1

    head, dirty = _market_state(market_repo)
    if dirty:
        print(
            "[FAIL] 市场仓工作区不干净（存在未提交改动）：快照内容必须来自已提交的市场状态，"
            "否则钉值无法与快照同源。请先在市场仓提交或还原变更。", file=sys.stderr,
        )
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
                if not d.exists() or not _content_equal(s.read_bytes(), d.read_bytes()):
                    stats["updated"] += 1
            elif s.is_dir():
                for child in s.rglob("*"):
                    if child.is_file():
                        rel = child.relative_to(s)
                        t = d / rel
                        if not t.exists() or not _content_equal(child.read_bytes(), t.read_bytes()):
                            stats["updated"] += 1
        print(f"[DRY-RUN] 将更新 {stats['updated']} 项，删除 {stats['removed']} 项（未写盘）")
        if head and ref_file.is_file():
            current = ref_file.read_text(encoding="utf-8").strip()
            if current != head:
                print(f"[DRY-RUN] 市场钉值将更新: {current[:12]}… -> {head}")
        elif head is None:
            print(f"[DRY-RUN] 市场仓非 git 检出，钉值不会更新：{ref_file}")
        return 0

    stats = {"updated": 0, "removed": 0}
    _mirror(market_repo, dest, stats)
    if head is None:
        print(f"[WARN] 市场仓非 git 检出（或 git 不可用），跳过钉值更新：{ref_file}", file=sys.stderr)
    elif not ref_file.is_file():
        print(f"[WARN] 钉值文件不存在，跳过钉值更新：{ref_file}", file=sys.stderr)
    else:
        _bump_market_pin(ref_file, head)
    print(
        f"[OK] 已同步离线快照：{market_repo} -> {dest}\n"
        f"     更新 {stats['updated']} 项，删除 {stats['removed']} 项\n"
        f"     内容：catalog.json + authors/ + keys/（不含插件/模板载荷）\n"
        f"     下一步：git add market/ && git commit -m 'chore(market): 同步离线快照'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
