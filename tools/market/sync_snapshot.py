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
  python tools/market/sync_snapshot.py                       # 默认 ../OmniCrawler-market -> ./market
  python tools/market/sync_snapshot.py --market-repo <路径>  # 指定市场仓
  python tools/market/sync_snapshot.py --check               # 同步前先在市场仓跑 catalog --check
  python tools/market/sync_snapshot.py --dry-run             # 只报告差异，不写盘

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

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKET_REPO = REPO_ROOT.parent / "OmniCrawler-market"


def _content_equal(left: bytes, right: bytes) -> bool:
    """**字节级**比对（不做任何换行归一化）。

    ★ 2026-11-27 修：这里原本做 `\r\n` → `\n` 归一化，理由是「Windows autocrlf
      会把 git 检出的 LF 转成 CRLF」。那个理由现在**不成立**：两个仓都有
      `.gitattributes` 的 `* text=auto eol=lf`，检出就是 LF。

      归一化的实际后果：市场仓里某个文件是 LF、快照里是旧的 CRLF 时，
      比对判定「相等」⇒ **不复制** ⇒ 快照里留着字节不同的陈旧文件。
      而这些文件的字节**进了 `package.manifest.json` 的 sha256 清单**，
      于是 `check_component_recipes` 报「清单与文件不一致」，且只发生在快照上
      （本地市场是好的，CI 读快照）—— 表现为「本地绿、CI 红」。

      实测（2026-11-27）：`deps.lock.json` 市场仓 sha256 `468ee94d…`（340 字节，LF），
      快照 `7194b5ca…`（351 字节，CRLF），归一化比对认为相等而跳过复制。

      判据：**快照必须逐字节等于市场仓**。字节完整性的唯一准据是字节本身。
    """
    return left == right
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

# 只需同步元数据：catalog + 发布者身份 + 信任根 + **组件声明**，绝不拉插件/模板载荷。
#
# ★ 为什么 `components` 也算元数据：组件目录**只含声明**——`recipe.json`（几十 KB，
#   描述"从哪个上游拉、sha256 是多少、怎么拼"）+ 签名轨 + `listing.md`。
#   真正的字节（wheel、模型、语言包）都在上游，本仓不托管。
#   当前两个组件合计 4.8 KB；把它们排除会让 CI 里的「装齐」拿不到 recipe。
SYNC_ITEMS = ("catalog.json", "authors", "keys", "components")


def _mirror(src: Path, dest: Path, stats: dict[str, int], *, top: bool = True) -> None:
    """把 src 目录镜像进 dest（src 有而 dest 无则拷贝，dest 多余则删除）。

    :param top: 是否是**顶层**调用。只有顶层才按 `SYNC_ITEMS` 过滤；
        递归进子目录时必须镜像全部内容。

        **为什么**：`components/` 下面是 `ocr-paddle/`、`tesseract-chi-sim/`，
        这些名字不在 `SYNC_ITEMS` 里。若递归时也按它过滤，子目录会被建成空壳
        —— 实测踩到：目录有了、recipe.json 没进去，CI 的「装齐」拿不到 recipe。
    """
    dest.mkdir(parents=True, exist_ok=True)
    items = SYNC_ITEMS if top else tuple(p.name for p in src.iterdir())
    # ★ 2026-11-27 修：**整个子目录从源里消失时，dest 里那份不会被删**。
    #
    #   原实现只在「某个源子目录内部」做「dest 多余项」清理（遍历的是 `src` 的
    #   孩子）。若一个子目录在源里**整体不存在**，它根本不在遍历范围内 ⇒
    #   dest 里那份永远留着。
    #
    #   实测：市场仓 `components/browser/` 在 6cc76ff 被 revert 后为空目录，
    #   而主仓 `market/components/browser/` 仍留着 3 个已撤回的内核组件（19 个文件），
    #   `sync_snapshot` 每次都报「删除 0 项」。
    #
    #   后果不是「多几个文件」：快照是**出包内容**，里面躺着三个从未验证成功的内核，
    #   `browser_kernels` 会照着它们列出「可装」——而市场里根本没有。
    #
    #   判据：镜像 = 源是准据。非顶层调用时，dest 里源已无的条目必须删掉。
    #   顶层不删（那里按 `SYNC_ITEMS` 白名单工作，删别的会误伤）。
    # ★ 而且**空目录不算存在**：git 不跟踪空目录，市场仓 revert 后 `components/browser/`
    #   只是磁盘上的空壳（git 里 0 个文件），若按「名字在就算存在」判定，
    #   快照里那份永远删不掉。判据：子目录里**没有任何文件** ⇒ 视为不存在。
    def _has_files(path: Path) -> bool:
        return any(p.is_file() for p in path.rglob("*"))

    if not top:
        src_names = {p.name for p in src.iterdir() if not p.is_dir() or _has_files(p)}
        for old in list(dest.iterdir()):
            if old.name not in src_names:
                if old.is_dir():
                    shutil.rmtree(old)
                else:
                    old.unlink()
                stats["removed"] += 1
    for item in items:
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
                    _mirror(child, target, stats, top=False)
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
