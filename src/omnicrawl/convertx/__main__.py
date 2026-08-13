"""ConvertX 命令行入口：`python -m omnicrawl.convertx SRC DST [--on-progress log|log-verbose]`.

功能：
- 最小 2 个位置参数：SRC 输入文件、DST 输出文件
- 格式按扩展名自动推断，可用 --from / --to 显式指定（csv/jsonl/xlsx/parquet/duckdb）
- --on-progress：
    * log（默认）：每阶段末尾一行 + 最终结果行（避免刷屏）
    * log-verbose：每批都打（调试或批处理观察）
- --table / --compression / --flat / --nested / --on-error 与 Python API 对齐

示例：
    python -m omnicrawl.convertx data.csv data.parquet --on-progress log
    python -m omnicrawl.convertx records.duckdb out.jsonl --table records
    python -m omnicrawl.convertx in.jsonl out.xlsx --on-error abort
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import (
    ConvertResult,
    TaskProgressEvent,
    convert,  # lazy-import OK，避免 __init__ 里启动时执行所有 register
)


def _build_progress_printer(
    *, mode: str, out: Callable[[str], None]
) -> Callable[[TaskProgressEvent], None]:
    """根据 --on-progress 返回 TaskProgressEvent 回调。"""
    if mode == "none":
        return lambda _ev: None
    last_stage: dict[str, str] = {"stage": "", "state": ""}

    def _format_eta(sec: float) -> str:
        sec = max(0, int(sec or 0))
        if sec <= 0:
            return "--"
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h{m:02d}m{s:02d}s"
        if m:
            return f"{m}m{s:02d}s"
        return f"{s}s"

    def _print(ev: TaskProgressEvent) -> None:
        stage_label = ev.display_stage or ev.stage or "pipeline"
        pct = max(0.0, min(100.0, float(ev.percent or 0.0)))
        # 关键状态点：阶段切换、完成/失败/取消、或 verbose 模式才逐批打印
        state_transition = (
            last_stage["stage"] != ev.stage
            or last_stage["state"] != ev.state
        )
        terminal_state = ev.state in {"finished", "failed", "cancelled"}
        verbose = mode == "log-verbose"

        if verbose or state_transition or terminal_state:
            items_hint = ""
            if ev.item_total and ev.item_total > 0:
                items_hint = f" items={ev.item_current}/{ev.item_total}"
            eta = _format_eta(ev.eta_seconds)
            out(
                f"[cx] {ev.state:<9s} {pct:5.1f}%  stage={stage_label}"
                f"{items_hint}  rate={ev.rate:.3f}{ev.rate_unit or '%/s'}"
                f"  ETA={eta}"
                + (f"  msg={ev.message}" if ev.message else "")
            )
        last_stage["stage"] = ev.stage or ""
        last_stage["state"] = ev.state or ""

    return _print


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m omnicrawl.convertx",
        description="OmniCrawler ConvertX：任意表格格式互转（CSV/JSONL/XLSX/Parquet/DuckDB）",
    )
    parser.add_argument("source", nargs="?", default=None, help="源文件路径（--list-paths 时可省略）")
    parser.add_argument("target", nargs="?", default=None, help="目标文件路径（--list-paths 时可省略）")
    parser.add_argument(
        "--from", dest="src_format", default=None,
        help="显式指定源格式（csv/jsonl/xlsx/parquet/duckdb），默认按扩展名推断",
    )
    parser.add_argument(
        "--to", dest="dst_format", default=None,
        help="显式指定目标格式（csv/jsonl/xlsx/parquet/duckdb），默认按扩展名推断",
    )
    parser.add_argument("--table", default="records", help="DuckDB 读写的表名（默认 records）")
    parser.add_argument(
        "--compression", default="zstd",
        help="Parquet 压缩：zstd/snappy/gzip/none（默认 zstd）",
    )
    parser.add_argument(
        "--flat", action="store_true", default=False,
        help="读 JSONL：把 pipeline 原始 .data 展开为 flat 列（默认行为，显式保留）",
    )
    parser.add_argument(
        "--nested", action="store_true", default=False,
        help="写 JSONL：按 pipeline 原始 records.jsonl 嵌套结构（含 data/evidence 字段）",
    )
    parser.add_argument(
        "--on-error", default="skip", choices=["skip", "abort"],
        help="解析/转换单条错误的策略（skip=跳过，abort=立即中止）",
    )
    parser.add_argument(
        "--on-progress", default="log",
        choices=["none", "log", "log-verbose"],
        help="进度输出模式：log（默认，阶段切换 + 完成）| log-verbose（每批）| none（静默）",
    )
    parser.add_argument(
        "--options-json", default=None,
        help="JSON 字符串形式的高级 options（会覆盖同名 CLI 标志），如 "
             "'{\"encoding\": \"gbk\", \"sheet\": \"Sheet2\"}'",
    )
    parser.add_argument(
        "--list-paths", action="store_true", default=False,
        help="仅列出所有转换路径（源格式 → 目标格式，含可用性）并退出",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.list_paths:
        from .paths import describe

        print(describe())
        return 0

    if not args.source or not args.target:
        print("[cx] ERROR: 缺少必填参数 source / target（或使用 --list-paths 查看转换路径）", file=sys.stderr)
        return 2

    src = Path(args.source).expanduser().resolve()
    dst = Path(args.target).expanduser().resolve()
    if not src.is_file():
        print(f"[cx] ERROR: 源文件不存在或非文件: {src}", file=sys.stderr)
        return 2

    # 合并 options：CLI 标志 → 基础 options；--options-json 优先覆盖
    options: dict[str, Any] = {
        "table": args.table,
        "compression": args.compression,
        "on_error": args.on_error,
    }
    # 顶层 convert() 参数：flat/nested 作为 convert 参数，不进 options
    flat = args.flat if args.flat else True  # 默认 flat=True
    nested = bool(args.nested)
    if args.options_json:
        try:
            extra = json.loads(args.options_json)
        except json.JSONDecodeError as exc:
            print(f"[cx] ERROR: --options-json 解析失败: {exc}", file=sys.stderr)
            return 2
        if isinstance(extra, dict):
            for k, v in extra.items():
                options[k] = v
        else:
            print("[cx] ERROR: --options-json 必须是 JSON 对象", file=sys.stderr)
            return 2

    # 进度回调
    def _stdout(line: str) -> None:
        try:
            print(line, flush=True)
        except Exception:  # noqa: BLE001
            pass

    on_progress = _build_progress_printer(mode=args.on_progress, out=_stdout)

    try:
        result: ConvertResult = convert(
            src,
            dst,
            src_format=args.src_format,
            dst_format=args.dst_format,
            options=options,
            flat=flat,
            nested=nested,
            table=args.table,
            compression=args.compression,
            on_progress=on_progress,
            on_error=args.on_error,
        )
    except ModuleNotFoundError as exc:
        missing = exc.name or str(exc)
        print(
            f"[cx] ERROR: 缺少可选依赖 '{missing}'。该格式需要额外安装："
            f"pip install {missing}（或 pip install omnicrawl[convertx-all]）",
            file=sys.stderr,
        )
        return 3
    except FileNotFoundError as exc:
        print(f"[cx] ERROR: 文件未找到: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[cx] ERROR: 参数/数据错误: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("\n[cx] 已中断 (cancelled)", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"[cx] ERROR: 转换失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    summary_payload = {
        "source": str(src),
        "target": str(dst),
        "source_format": result.source_format,
        "target_format": result.target_format,
        "rows": result.rows,
        "columns": result.columns,
        "warnings": result.warnings,
        "extra": result.extra or {},
    }
    if args.on_progress != "none":
        print("[cx] OK  " + json.dumps(summary_payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
