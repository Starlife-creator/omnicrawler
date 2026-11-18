"""W5.1: controlled public re-test of the originally-reported scenarios (N1b).

Deliberately **not** part of unconditional CI: it talks to real public sites, so it runs
on demand with固定 URL 子集 + 超时重试 + 限速, and writes a record that goes into
《审查记录》.  The three sites are read-only sandboxes with no anti-scraping measures:

* `books.toscrape.com`   -- 静态列表
* `quotes.toscrape.com`  -- 分页（作者名跨页重复，正是"同标题不同页面不误合并"的样本）
* `http://iana.org/`     -- 重定向：**一个 URL 同时覆盖 apex→www 与 http→https**
                            （实测 `http://iana.org/` → `https://www.iana.org/`）

判据（照 §6.4 W5.1 原文）：

1. 重定向别名在真实 apex↔www、http→https 上成立
2. 同标题不同页面**不误合并**
3. 取消↔恢复与不中断运行**一致**（记录集合 + 产物摘要）
4. 记录写进《审查记录》（本工具输出 JSON，由调用方落到文档）

Output is ASCII only (same discipline as the other `tools/` helpers).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# 固定 URL 子集（自选三家，只读、无反爬、结构稳定）
STATIC_LIST = "https://books.toscrape.com/"
PAGINATION = "https://quotes.toscrape.com/page/{page}/"
REDIRECT_APEX = "http://iana.org/"
REDIRECT_FINAL_PREFIX = "https://www.iana.org"

# 限速与重试（受控执行）
DELAY_SECONDS = 1.0
ATTEMPTS = 3
BACKOFF_SECONDS = 2.0


def _run(argv: list[str], cwd: Path, *, timeout: int = 420) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(  # noqa: S603 - argv is built from literals plus paths we own
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )


def _cli(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, "-m", "omnicrawler.cli", *args], work_dir)


def _config_text(work_dir: Path, name: str, seed: str, *, item_selector: str, fields: str,
                 max_pages: int) -> str:
    return f"""project:
  name: {name}
  workspace: {(work_dir / name).resolve().as_posix()}
source:
  kind: static_html
  seeds: [{seed}]
crawl: {{max_pages: {max_pages}, same_host: false}}
http: {{allow_private_network: true, respect_robots: true, delay_seconds: {DELAY_SECONDS}}}
extract:
  mode: html
  item_selector: "{item_selector}"
  fields:
{fields}
"""


def _with_retry(label: str, action):
    """超时重试：固定次数 + 退避（受控执行的要求之一）。"""
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - 重试后仍失败才抛出
            last = exc
            print(f"  ! {label} 第 {attempt}/{ATTEMPTS} 次失败: {type(exc).__name__}: {exc}")
            if attempt < ATTEMPTS:
                time.sleep(BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"{label} 重试 {ATTEMPTS} 次仍失败: {last}")


def _records(workspace: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(workspace.rglob("records.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _record_key(record: dict[str, Any]) -> str:
    data = record.get("data") or {}
    return json.dumps(data, ensure_ascii=True, sort_keys=True)


def _digest(records: list[dict[str, Any]]) -> str:
    joined = "\n".join(sorted(_record_key(r) for r in records))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _crawl(work_dir: Path, name: str, seed: str, *, item_selector: str, fields: str,
           max_pages: int) -> list[dict[str, Any]]:
    config = work_dir / f"{name}.yaml"
    config.write_text(
        _config_text(work_dir, name, seed, item_selector=item_selector, fields=fields,
                     max_pages=max_pages),
        encoding="utf-8",
    )
    completed = _cli(work_dir, "run", "-c", str(config.resolve()))
    if completed.returncode != 0:
        raise RuntimeError(
            f"{name} 抓取失败:\n" + (completed.stdout + completed.stderr).strip()[-4000:]
        )
    return _records((work_dir / name).resolve())


# ---------------------------------------------------------------- checks


def check_redirect_alias(work_dir: Path) -> dict[str, Any]:
    """① 真实 apex→www + http→https：交付记录的 source_url 必须是**确认后的最终 URL**。"""
    print("\n[1/3] 重定向别名（http://iana.org/ -> https://www.iana.org/）")
    records = _with_retry(
        "重定向抓取",
        lambda: _crawl(work_dir, "redirect", REDIRECT_APEX, item_selector="a",
                       fields='    label: {selector: ""}', max_pages=1),
    )
    if not records:
        raise RuntimeError("重定向站点没有交付任何记录 ⇒ 无法验证别名")
    sources = sorted({str(r.get("source_url", "")) for r in records})
    bad = [s for s in sources if not s.startswith(REDIRECT_FINAL_PREFIX)]
    if bad:
        raise RuntimeError(
            f"source_url 不是确认后的最终 URL（别名未登记在精确最终 URL 上）: {bad[:5]}"
        )
    print(f"  OK 交付 {len(records)} 条，source_url 全部落在 {REDIRECT_FINAL_PREFIX}")
    return {"records": len(records), "source_urls": sources[:3], "a_sources": sources}


def check_no_false_merge(work_dir: Path) -> dict[str, Any]:
    """② 同标题不同页面不误合并：作者名跨页重复，两页各 10 条都必须保留。"""
    print("\n[2/3] 同标题不同页面不误合并（quotes 第 1、2 页，作者跨页重复）")
    fields = '    text: {selector: "span.text"}\n    author: {selector: "small.author"}'
    pages: dict[int, list[dict[str, Any]]] = {}
    for page in (1, 2):
        pages[page] = _with_retry(
            f"第 {page} 页",
            lambda p=page: _crawl(work_dir, f"pagination-p{p}", PAGINATION.format(page=p),
                                  item_selector="div.quote", fields=fields, max_pages=1),
        )
    counts = {p: len(v) for p, v in pages.items()}
    if any(c == 0 for c in counts.values()):
        raise RuntimeError(f"分页站点某页没有交付记录: {counts}")
    authors = {
        p: {str((r.get("data") or {}).get("author", "")) for r in v} for p, v in pages.items()
    }
    overlap = authors[1] & authors[2]
    # ★ 判据：两页各 10 条；且存在跨页重复的作者 —— 若实现按"标题/作者"去重，第二页会少记录。
    if counts[1] != counts[2]:
        raise RuntimeError(f"两页交付条数不一致（可能被合并/去重）: {counts}")
    if not overlap:
        raise RuntimeError("样本失效：两页没有重复作者 ⇒ 这条判据无法证明「不误合并」")
    print(f"  OK 两页各 {counts[1]} 条；跨页重复作者 {len(overlap)} 个，未被合并")
    return {"counts": counts, "shared_authors": sorted(overlap)[:5],
            "distinct_authors": {p: len(a) for p, a in authors.items()}}


def _dataset_fingerprint(workspace: Path) -> dict[str, int]:
    """交付**数据集**的指纹（来源 URL -> 条数），取自 state store。

    ★ 教训（本次实测）：`output/records.jsonl` 是**单次 run 的导出产物** ——
    中断 run 交付 page1-2、`resume` 交付 page3-4 之后，它里面**只有 page3-4**。
    若拿它比对，会得出"恢复后丢了记录"的错误结论；而产品自己的 `control status`
    （`totals.records`）与 state store 的 `records` 表都显示 **40 条齐全**。
    ⇒ 跨 run 的一致性必须在**数据集**这一层判定。
    """
    db = workspace / "state.sqlite3"
    if not db.is_file():
        raise RuntimeError(f"没有 state store，无法判定数据集: {db}")
    import sqlite3

    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return {str(url): int(n) for url, n in con.execute(
            "SELECT source_url, COUNT(*) FROM records GROUP BY source_url"
        )}
    finally:
        con.close()


def _status_totals(work_dir: Path, config: Path) -> int:
    """产品自己报的交付总数（`control status` 的 `totals.records`）。"""
    completed = _cli(work_dir, "control", "status", "-c", str(config.resolve()))
    text = completed.stdout or ""
    start = text.find("{")
    if start < 0:
        raise RuntimeError("control status 没有输出 JSON")
    payload = json.loads(text[start:])
    return int(payload.get("totals", {}).get("records", -1))


def check_resume_matches_uninterrupted(work_dir: Path) -> dict[str, Any]:
    """③ 取消↔恢复 == 不中断：用 `--max-pages` 制造**未完成**run，再 `resume` 到同一终点。

    判据落在**数据集**上（来源->条数 指纹 + 产品自报总数），不是单次 run 的导出文件。
    """
    print("\n[3/3] 取消↔恢复 == 不中断（4 个固定 URL，各 10 条）")
    seeds = "\n".join(f"    - {PAGINATION.format(page=p)}" for p in (1, 2, 3, 4))
    fields = '    text: {selector: "span.text"}'

    full_ws = (work_dir / "resume-full").resolve()
    full_cfg = work_dir / "resume-full.yaml"
    full_cfg.write_text(
        _config_text(work_dir, "resume-full", PAGINATION.format(page=1),
                     item_selector="div.quote", fields=fields, max_pages=4).replace(
            f"  seeds: [{PAGINATION.format(page=1)}]\n",
            "  seeds:" + chr(10) + seeds + chr(10)).replace("kind: static_html", "kind: url_list"),
        encoding="utf-8",
    )

    def _full() -> dict[str, int]:
        completed = _with_retry(
            "不中断运行",
            lambda: _cli(work_dir, "run", "-c", str(full_cfg.resolve())),
        )
        if completed.returncode != 0:
            raise RuntimeError("不中断运行失败:\n" + (completed.stdout + completed.stderr)[-3000:])
        return _dataset_fingerprint(full_ws)

    full = _full()

    partial_ws = (work_dir / "resume-partial").resolve()
    partial_cfg = work_dir / "resume-partial.yaml"
    partial_cfg.write_text(
        _config_text(work_dir, "resume-partial", PAGINATION.format(page=1),
                     item_selector="div.quote", fields=fields, max_pages=4).replace(
            f"  seeds: [{PAGINATION.format(page=1)}]\n",
            "  seeds:" + chr(10) + seeds + chr(10)).replace("kind: static_html", "kind: url_list"),
        encoding="utf-8",
    )

    def _partial_then_resume() -> dict[str, int]:
        first = _cli(work_dir, "run", "-c", str(partial_cfg.resolve()), "--max-pages", "2")
        if first.returncode != 0:
            raise RuntimeError("中断运行失败:\n" + (first.stdout + first.stderr)[-3000:])
        paused = _cli(work_dir, "control", "status", "-c", str(partial_cfg.resolve()))
        if "pending" not in (paused.stdout or ""):
            raise RuntimeError("中断后 status 没有显示 pending ⇒ 这次运行其实是「已完成」，本判据会空转")
        resumed = _cli(work_dir, "resume", "-c", str(partial_cfg.resolve()), "--max-pages", "4")
        if resumed.returncode != 0:
            raise RuntimeError("resume 失败:\n" + (resumed.stdout + resumed.stderr)[-3000:])
        return _dataset_fingerprint(partial_ws)

    resumed = _with_retry("中断+resume", _partial_then_resume)

    full_total = sum(full.values())
    if full != resumed:
        raise RuntimeError(
            "取消↔恢复与不中断运行的数据集不一致：\n"
            f"  不中断: {full}\n  恢复后: {resumed}"
        )
    print(f"  OK 数据集一致：{full_total} 条 / {len(full)} 个来源，"
          f"产品自报 totals.records={_status_totals(work_dir, partial_cfg)}")
    return {"records": full_total, "by_source": full,
            "status_totals_full": _status_totals(work_dir, full_cfg),
            "status_totals_resumed": _status_totals(work_dir, partial_cfg)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="工作目录（放在项目内，例如 .audit-tmp/w51）")
    parser.add_argument("--report", type=Path, help="把结果 JSON 写到这里（供《审查记录》引用）")
    parser.add_argument("--only", choices=("1", "2", "3"), help="只跑某一项判据")
    args = parser.parse_args(argv)
    args.work_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"sites": [STATIC_LIST, PAGINATION.format(page=1), REDIRECT_APEX]}
    checks = {
        "1": ("redirect_alias", check_redirect_alias),
        "2": ("no_false_merge", check_no_false_merge),
        "3": ("resume_matches_uninterrupted", check_resume_matches_uninterrupted),
    }
    for key, (name, fn) in checks.items():
        if args.only and args.only != key:
            continue
        manifest[name] = fn(args.work_dir)

    payload = json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True)
    print("\n" + payload)
    if args.report:
        args.report.write_text(payload + "\n", encoding="utf-8")
        print(f"report written: {args.report}")
    print("public re-test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
