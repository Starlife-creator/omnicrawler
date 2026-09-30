#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""产出**变更包**（change package）：本版相对某个旧版本"变了哪些文件"的那一份字节。

为什么需要它
------------
`update.json` 的 `payload.delta` 只是**声明**"某个旧版本有对应变更包"，而此前**没有任何东西
会产出那个包** —— 只有消费端。于是"小版本只发增量、老用户不必下整包"这条路径永远走不到，
客户端每次都退化成全量下载。

它由什么算出来（**不需要重建旧版本**）
------------------------------------
* 新版的逐文件哈希 ← 本版**归档**（`--payload-archive`，正是要发布的那个包）；
* 旧版的逐文件哈希 ← 旧版的**已签名清单**（几十~几百 KB，`--baseline-manifest`）。

> ★ 关键澄清：不需要对比两版**构建产物**，也不需要把旧版搬回来。客户端判断"哪些文件要下"
> 靠的是**逐文件哈希**，而旧版的哈希就在旧版的清单里。只有**二进制差分**
> （bsdiff / `zstd --patch-from` 那种"100 MB 的改动只传几 MB"）才需要旧版的**字节** ——
> 那条路要额外维护基线缓存（本机实测 Electron 系就把上一版 506 MB 整包缓存着当基线），
> 与本项目"不留整份副本"的取向相悖，故**不做**。

成本与"K 个基线"
---------------
一次构建 + K 份旧清单 ⇒ **K 个变更包**（K＝窗口大小，默认 3）。每个包只装
"新增的 + 哈希不同的"成员。为什么要有窗口：用户不一定逐版升级；为什么不能无限大：
每个基线都必须在**发布时预先算好并上传**（静态托管不能"有人来要时现算"），
而更老的用户本来就可以走全量（每版都发全量包 ⇒ 不会卡住，只是多下）。

用法::

    python tools/build_update_delta.py \\
        --payload-archive dist/OmniCrawler-0.15.3-Linux-Portable-Standard.tar.xz \\
        --version 0.15.3 --platform linux --edition standard \\
        --baseline-manifest 0.15.2=update-linux-standard.json \\
        --baseline-manifest 0.15.1=update-linux-standard.json \\
        --out-dir dist --emit-args dist/.delta-args-linux-standard.txt

随后把 `--emit-args` 写出的文件交给清单工具：`build_update_manifest.py --delta-file <它>`。

删除清单**刻意不在这里产出**：`payload.deleted` 只能来自**已签名清单**
（见 `build_update_manifest.py --previous-manifest`）。变更包是**不可信容器**，
让不可信数据驱动删除等于给"随便删用户文件"开口子。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

sys.path.insert(0, str(Path(__file__).resolve().parent))


from manifest_common import (  # noqa: E402
    DEFAULT_TRUST_ROOT,
    FEED_FILENAME,
    PayloadEntry,
    iter_archive_members,
    load_signed_manifest,
    require_single_root,
    resolve_trusted_key,
)

#: 变更包内的成员时间戳固定为 1980-01-01（zip 纪元起点）⇒ 同样输入产出**同样字节**，
#: 否则每次重跑哈希都变，"发布产物可复现"这条判据就失效了。
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

#: 没记录权限位时的兜底模式（普通文件）。
DEFAULT_MODE = 0o644


def _write_member(target: zipfile.ZipFile, relative: str, mode: int | None, body: bytes) -> None:
    info = zipfile.ZipInfo(relative, date_time=FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = ((mode or DEFAULT_MODE) & 0o777) << 16
    with target.open(info, "w") as handle:
        handle.write(body)


def build(
    *,
    archive: Path,
    baselines: list[tuple[str, dict[str, PayloadEntry]]],
    out_dir: Path,
    version: str,
    platform: str,
    edition: str,
) -> list[tuple[str, Path, int]]:
    """单次遍历归档，为每个基线写出一个变更包；返回 ``[(旧版本, 包路径, 成员数)]``。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{platform}-{edition}" if edition else f"-{platform}"
    outputs: dict[str, Path] = {}
    members: dict[str, int] = {}
    handles: dict[str, zipfile.ZipFile] = {}
    roots: set[str] = set()
    seen: dict[str, str] = {}
    try:
        for name, _entries in baselines:
            outputs[name] = out_dir / f"update-{name}-to-{version}{suffix}.zip"
            members[name] = 0
            handles[name] = zipfile.ZipFile(outputs[name], "w", zipfile.ZIP_DEFLATED)
        for root, relative, mode, stream in iter_archive_members(archive):
            # ★ 必须先读完这个成员才能判定它属不属于某个包，而 tar.xz **不能回头读**
            #   ⇒ 成员内容先缓存在内存里。峰值＝**单个最大成员**（本项目的最大件是
            #   Chromium/OCR 的二进制，百 MB 量级），不是整包大小。
            roots.add(root)
            head = stream.read(1024 * 1024)
            digest = hashlib.sha256(head)
            total = len(head)
            body_chunks = [head] if head else []
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                total += len(chunk)
                body_chunks.append(chunk)
            sha = digest.hexdigest()
            if relative in seen:
                # tar 允许重名（后者胜）⇒ 那样"这份包的成员集合"会有歧义，直接拒绝。
                raise SystemExit(
                    f"归档里出现重名成员: {relative}"
                    f"（前一次 {seen[relative][:12]}…，本次 {sha[:12]}…）"
                )
            seen[relative] = sha
            for name, entries in baselines:
                previous = entries.get(relative)
                if previous is not None and previous.sha256 == sha and previous.size == total:
                    continue  # 与这个基线一致 ⇒ 不进这个包
                _write_member(handles[name], relative, mode, b"".join(body_chunks))
                members[name] += 1
    finally:
        for handle in handles.values():
            handle.close()
    require_single_root(roots, what=str(archive))

    produced: list[tuple[str, Path, int]] = []
    for name, _entries in baselines:
        path = outputs[name]
        if members[name] == 0:
            # 与基线逐字节一致 ⇒ 不产出空包（客户端会自然走全量），并如实说明
            path.unlink(missing_ok=True)
            print(f"[跳过] {name}: 与本版**完全一致**，不产出变更包", file=sys.stderr)
            continue
        produced.append((name, path, members[name]))
    return produced


def _fetch_baseline(
    *, url_base: str, version: str, platform: str, edition: str, scratch: Path
) -> tuple[str, Path] | None:
    """按 (平台, 版本) 精度逐级取回某版本的**已签名清单**，落盘到 ``scratch``。

    为什么落盘再交给清单工具：清单工具要用同一份字节算"已移除"清单；让两个工具各自
    再去取一次会多一次网络往返，还可能取到**不同**的两次发布内容（发布过程中重传资产）。
    取回失败一律**跳过**（打日志），不打断发布 —— 某个老版本没有清单是正常情形
    （0.15.0 之前根本没有清单，或那次是 pre-release）。
    """
    from manifest_common import feed_filename, read_source

    base = url_base.rstrip("/")
    source_dir = f"{base}/v{version}"
    # 与客户端同一套回退：版本级 → 平台级 → 通用
    names = [feed_filename(platform, edition), feed_filename(platform), FEED_FILENAME]
    for index, name in enumerate(names):
        if index and names[index - 1] == name:
            continue
        url = f"{source_dir}/{name}"
        try:
            body = read_source(url)
        except SystemExit:
            continue
        saved = scratch / f".baseline-{version}.json"
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(body)
        print(f"[基线] {version}: 取到 {url}")
        return version, saved
    print(f"[基线] {version}: 该版本没有可用的更新清单 ⇒ 跳过", file=sys.stderr)
    return None


def _resolve_baselines(
    *, args: argparse.Namespace, trusted: bytes, scratch: Path
) -> tuple[list[tuple[str, dict[str, PayloadEntry]]], list[tuple[str, Path]]]:
    """把 ``--baseline-manifest`` 与 ``--baseline-version`` 解析成统一的基线集合。"""
    specs = list(args.baseline_manifest or [])
    if args.baselines:
        if not args.baseline_url_base:
            raise SystemExit("--baselines 需要配合 --baseline-url-base 使用")
        for raw in str(args.baselines).split(","):
            version = raw.strip()
            if not version:
                continue
            found = _fetch_baseline(
                url_base=args.baseline_url_base,
                version=version,
                platform=str(args.platform).strip().lower(),
                edition=str(args.edition or "").strip().lower(),
                scratch=scratch,
            )
            if found is not None:
                specs.append(f"{found[0]}={found[1]}")

    usable: list[tuple[str, dict[str, PayloadEntry]]] = []
    saved: list[tuple[str, Path]] = []
    for spec in specs:
        name, feed, _body = load_signed_manifest(spec, trusted_public_key=trusted)
        saved.append((name, Path(str(spec).partition("=")[2].strip())))
        if not feed.payload_files:
            # 该基线没有逐文件清单（例如 macOS 的 assets-only 清单）⇒ 无法算差集。
            print(
                f"[跳过] {name}: 该清单没有逐文件清单（payload.files 为空），无法算变更集",
                file=sys.stderr,
            )
            continue
        usable.append(
            (
                name,
                {
                    relative: PayloadEntry(sha256=entry.sha256, size=entry.size)
                    for relative, entry in feed.payload_files.items()
                },
            )
        )
    return usable, saved


def main() -> int:
    parser = argparse.ArgumentParser(description="产出应用自更新的变更包（构建期运行，无需密钥）")
    parser.add_argument("--payload-archive", required=True, help="本版**归档本体**（zip / tar.xz）")
    parser.add_argument("--version", required=True, help="本版版本号（命名用）")
    parser.add_argument(
        "--platform",
        required=True,
        choices=("windows", "linux", "macos"),
        help="本包所属平台（命名用）",
    )
    parser.add_argument(
        "--edition",
        default="",
        choices=("", "standard", "full"),
        help="本包所属版本（命名用；Standard/Full 的载荷不同 ⇒ 变更包也不同）",
    )
    parser.add_argument(
        "--baseline-manifest",
        action="append",
        default=None,
        metavar="<旧版本>=<文件路径|URL>",
        help="旧版的**已签名清单**（可重复；必须验签）。",
    )
    parser.add_argument(
        "--baselines",
        default="",
        help=(
            "逗号分隔的旧版本号（如 `0.15.2,0.15.1,0.15.0`）。工具自己去发布页把它们的"
            "已签名清单取回来（按 版本级 → 平台级 → 通用 逐级回退）；取不到就**跳过**。"
            "配合 --baseline-url-base。K＝这里给几个 ⇒ 产出几个变更包。"
        ),
    )
    parser.add_argument(
        "--baseline-url-base",
        default="",
        help="发布下载基址（与 --baselines 一起用），如 https://github.com/<owner>/<repo>/releases/download",
    )
    parser.add_argument("--out-dir", default=".", help="变更包输出目录")
    parser.add_argument(
        "--scratch-dir",
        default="",
        help="取回的基线清单落盘目录（缺省＝out-dir）。★ 别用发布产物目录，免得被当资产上传。",
    )
    parser.add_argument(
        "--emit-args",
        default="",
        help=(
            "把结果写成 `<旧版本>=<包路径>` 行（供 build_update_manifest.py --delta-file 读取）。"
            "★ 写到你自己的临时目录，别写进发布产物目录（否则会被当成资产上传）。"
        ),
    )
    parser.add_argument(
        "--emit-previous-args",
        default="",
        help=(
            "把基线写成 `<旧版本>=<清单路径>` 行（供 build_update_manifest.py "
            "--previous-manifest-file 读取 ⇒ 累积算「本版已移除」）。"
        ),
    )
    parser.add_argument(
        "--trusted-public-key",
        default="",
        help=f"更新信任根（缺省用随包内置的 {DEFAULT_TRUST_ROOT}）",
    )
    args = parser.parse_args()

    if not (args.baseline_manifest or args.baselines):
        print("[错误] 至少要给 --baseline-manifest 或 --baselines", file=sys.stderr)
        return 2

    trusted = resolve_trusted_key(args.trusted_public_key)
    if trusted is None:
        print(
            f"[错误] 找不到更新信任根（{DEFAULT_TRUST_ROOT}）⇒ 拒绝拿未验签的清单当基线",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.out_dir).expanduser()
    scratch = Path(args.scratch_dir).expanduser() if args.scratch_dir else out_dir
    baselines, saved = _resolve_baselines(args=args, trusted=trusted, scratch=scratch)
    if not baselines:
        print("[错误] 没有任何可用的基线清单", file=sys.stderr)
        return 2

    archive = Path(args.payload_archive).expanduser()
    produced = build(
        archive=archive,
        baselines=baselines,
        out_dir=out_dir,
        version=str(args.version).strip(),
        platform=str(args.platform).strip().lower(),
        edition=str(args.edition).strip().lower(),
    )

    if args.emit_args:
        target = Path(args.emit_args).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(f"{name}={path.resolve()}\n" for name, path, _count in produced),
            encoding="utf-8",
        )
    if args.emit_previous_args:
        target = Path(args.emit_previous_args).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(f"{name}={path.resolve()}\n" for name, path in saved), encoding="utf-8"
        )
    for name, path, count in produced:
        print(f"[完成] 变更包 {name} → {path}（{count} 个成员，{path.stat().st_size} 字节）")
    if not produced:
        print("[信息] 没有任何基线产生变更包（本版与它们逐字节一致？）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
