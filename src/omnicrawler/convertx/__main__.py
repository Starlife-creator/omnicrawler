"""ConvertX 命令行入口：`python -m omnicrawler.convertx SRC DST [--on-progress log|log-verbose]`.

功能：
- 最小 2 个位置参数：SRC 输入文件、DST 输出文件
- 格式按扩展名自动推断，可用 --from / --to 显式指定（csv/jsonl/xlsx/parquet/duckdb）
- --on-progress：
    * log（默认）：每阶段末尾一行 + 最终结果行（避免刷屏）
    * log-verbose：每批都打（调试或批处理观察）
- --table / --compression / --flat / --nested / --on-error 与 Python API 对齐

示例：
    python -m omnicrawler.convertx data.csv data.parquet --on-progress log
    python -m omnicrawler.convertx records.duckdb out.jsonl --table records
    python -m omnicrawler.convertx in.jsonl out.xlsx --on-error abort
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import (
    ConversionCancelledError,
    ConvertResult,
    OutputRule,
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
        prog="python -m omnicrawler.convertx",
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
        help="仅列出所有转换路径（源格式 → 目标格式，含可用性与缺失依赖提示）并退出",
    )
    parser.add_argument(
        "--assume-installed", action="append", default=[], metavar="EXTRA",
        help="与 --list-paths 联用：模拟已安装某 extra/族/扩展名，预览新增路径（可多次）",
    )
    parser.add_argument(
        "--capabilities-json", action="store_true", default=False,
        help="与 --list-paths 联用：以 JSON 输出能力目录（含缺失依赖与解锁边）",
    )
    parser.add_argument(
        "--plan", action="store_true", default=False,
        help="仅打印 SRC → DST 的转换计划（边、代价、guard）并退出，不写文件",
    )
    parser.add_argument(
        "--max-steps", type=int, default=3,
        help="--plan 的最大步数（默认 3）",
    )
    parser.add_argument("--output-dir", default=None, help="输出目录（默认与 target 同目录）")
    parser.add_argument(
        "--on-conflict", choices=("overwrite", "rename", "skip", "error"), default=None,
        help="目标已存在时的策略：overwrite/rename/skip/error",
    )
    parser.add_argument(
        "--name-template", default=None,
        help="输出文件名模板，占位符 {name}{ext}{date}{time}{index}",
    )
    parser.add_argument("--preserve-timestamps", action="store_true", default=False, help="保留源文件时间戳")
    parser.add_argument("--preset", default=None, help="使用内置/文件中同名的具名配方")
    parser.add_argument("--preset-file", default=None, help="自定义 preset JSON 文件")
    parser.add_argument("--list-presets", action="store_true", default=False, help="列出可用配方并退出")
    parser.add_argument(
        "--watch", action="store_true", default=False,
        help="监听 source 目录：稳定落地的文件自动转换为 --to 指定格式",
    )
    parser.add_argument("--watch-output", default=None, help="监听模式的输出目录（默认与源同目录）")
    parser.add_argument(
        "--watch-settle", type=float, default=None,
        help="静置确认秒数（默认 Windows 2s / 其他平台 5s）",
    )
    parser.add_argument("--watch-interval", type=float, default=1.0, help="监听轮询间隔秒数（默认 1.0）")
    parser.add_argument("--watch-once", action="store_true", default=False, help="只扫描一轮后退出")
    parser.add_argument(
        "--watch-on-conflict", choices=("overwrite", "rename", "skip", "error"), default="rename",
        help="监听模式的输出冲突策略（默认 rename）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # 与主 CLI 一致：Windows 管道/重定向默认 GBK，中文标签与 →/↔ 会抛 UnicodeEncodeError。
    if isinstance(sys.stdout, io.TextIOWrapper) and isinstance(sys.stderr, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.list_paths:
        from .catalog import snapshot
        from .paths import describe

        try:
            if args.capabilities_json:
                payload = snapshot(assume_installed=args.assume_installed)
                print(json.dumps({
                    "families": [
                        {
                            "family": status.family,
                            "extensions": list(status.extensions),
                            "readable": status.readable,
                            "writable": status.writable,
                            "available": status.available,
                            "simulated": status.simulated,
                            "missing_formats": list(status.missing_formats),
                            "reason": status.reason,
                            "install_hint": status.install_hint,
                        }
                        for status in payload.families
                    ],
                    "missing": [
                        {
                            "extension": item.extension,
                            "readable": item.readable,
                            "writable": item.writable,
                            "can_read": item.can_read,
                            "can_write": item.can_write,
                            "reason": item.reason,
                            "install_hint": item.install_hint,
                            "unlocked_edges": list(item.unlocked_edges),
                        }
                        for item in payload.missing
                    ],
                    "unlocks": [
                        {
                            "install": hint.install,
                            "formats": list(hint.formats),
                            "edges": list(hint.edges),
                        }
                        for hint in payload.unlocks
                    ],
                }, ensure_ascii=False, indent=2))
            else:
                print(describe(assume_installed=args.assume_installed))
        except ValueError as exc:
            print(f"[cx] ERROR: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.plan:
        from . import sniff_format
        from .planner import describe_plan, plan_conversion

        src_fmt = (args.src_format or "").lower() or (
            sniff_format(Path(args.source)) if args.source else None
        )
        dst_fmt = (args.dst_format or "").lower() or (
            sniff_format(Path(args.target)) if args.target else None
        )
        if not src_fmt or not dst_fmt:
            print("[cx] ERROR: --plan 需要可识别的源/目标格式（可用 --from/--to 指定）", file=sys.stderr)
            return 2
        try:
            plan = plan_conversion(
                src_fmt,
                dst_fmt,
                max_steps=args.max_steps,
                assume_installed=args.assume_installed,
            )
        except ValueError as exc:
            print(f"[cx] ERROR: {exc}", file=sys.stderr)
            return 2
        print(describe_plan(plan))
        return 0 if plan is not None else 1

    if args.list_presets:
        from .presets import available_presets

        try:
            presets = available_presets(args.preset_file)
        except (ValueError, KeyError) as exc:
            print(f"[cx] ERROR: {exc}", file=sys.stderr)
            return 2
        for name, item in presets.items():
            warnings = f"  [warn: {'；'.join(item.warnings)}]" if item.warnings else ""
            print(f"{name}\t{item.description}{warnings}")
        return 0

    if not args.source or (not args.watch and not args.target):
        print("[cx] ERROR: 缺少必填参数 source / target（或使用 --list-paths 查看转换路径）", file=sys.stderr)
        return 2

    src = Path(args.source).expanduser().resolve()
    dst = Path(args.target).expanduser().resolve() if args.target else None

    preset: Any = None
    if args.preset_file or args.preset:
        if not args.preset:
            print("[cx] ERROR: --preset-file 需要同时指定 --preset NAME", file=sys.stderr)
            return 2
        from .presets import get_preset

        try:
            preset = get_preset(args.preset, args.preset_file)
        except (KeyError, ValueError) as exc:
            print(f"[cx] ERROR: {exc}", file=sys.stderr)
            return 2
        for warning in preset.warnings:
            print(f"[cx] WARN: {warning}", file=sys.stderr)

    preset_kwargs = dict(preset.convert_kwargs) if preset else {}
    options: dict[str, Any] = dict(preset.options) if preset else {}
    for key, default in (("table", "records"), ("compression", "zstd"), ("on_error", "skip")):
        cli_value = getattr(args, key)
        if cli_value != default:
            options[key] = cli_value
        else:
            options.setdefault(key, preset_kwargs.get(key, default))
    # 顶层 convert() 参数：flat/nested 作为 convert 参数，不进 options
    flat = True if args.flat else bool(preset_kwargs.get("flat", True))
    nested = bool(args.nested or preset_kwargs.get("nested", False))
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

    rule_kwargs = dict(preset.output_rule) if preset else {}
    if args.output_dir:
        rule_kwargs["directory"] = args.output_dir
    if args.on_conflict:
        rule_kwargs["on_conflict"] = args.on_conflict
    if args.name_template:
        rule_kwargs["template"] = args.name_template
    if args.preserve_timestamps:
        rule_kwargs["preserve_timestamps"] = True
    output_rule = OutputRule(**rule_kwargs) if rule_kwargs else None

    if args.watch:
        if not src.is_dir():
            print(f"[cx] ERROR: --watch 需要 source 是目录: {src}", file=sys.stderr)
            return 2
        target_format = args.dst_format or (dst.suffix if dst else "")
        if not target_format:
            print("[cx] ERROR: --watch 需要 --to 指定目标格式（如 --to .xlsx）", file=sys.stderr)
            return 2
        target_dir = Path(args.watch_output).expanduser().resolve() if args.watch_output else src
        target_dir.mkdir(parents=True, exist_ok=True)
        from dataclasses import replace

        from .watch import FolderWatcher, WatchConfig

        try:
            config = WatchConfig(
                source_dir=src,
                target_dir=target_dir,
                target_format=target_format,
                settle_seconds=args.watch_settle,
                poll_interval=args.watch_interval,
                on_conflict=args.watch_on_conflict,
                preserve_timestamps=bool(
                    preset.output_rule.get("preserve_timestamps", True) if preset else True
                ),
            )
        except ValueError as exc:
            print(f"[cx] ERROR: {exc}", file=sys.stderr)
            return 2

        def _watch_convert(source_path: Path, target_path: Path, rule: Any) -> Any:
            if preset and "template" in preset.output_rule:
                rule = replace(rule, template=str(preset.output_rule["template"]))
            return convert(
                source_path,
                target_path,
                options=options,
                flat=flat,
                nested=nested,
                table=options.get("table", args.table),
                compression=options.get("compression", args.compression),
                on_error=options.get("on_error", args.on_error),
                output_rule=rule,
            )

        watcher = FolderWatcher(config, converter=_watch_convert)
        if args.watch_once:
            events = watcher.scan_once()
            watch_payload = {
                "ok": all(event.status != "error" for event in events),
                "mode": "watch-once",
                "source": str(src),
                "target_format": config.target_format,
                "events": [
                    {
                        "source": str(event.source),
                        "status": event.status,
                        "output": str(event.output) if event.output else None,
                        "error": event.error,
                        "warnings": list(event.warnings),
                    }
                    for event in events
                ],
            }
            print(json.dumps(watch_payload, ensure_ascii=False))
            return 0 if watch_payload["ok"] else 1

        def _log_event(event: Any) -> None:
            target = f" -> {event.output}" if event.output else ""
            if event.status == "error":
                print(f"[cx] ERROR {event.source}: {event.error}", file=sys.stderr)
            else:
                print(f"[cx] {event.status} {event.source}{target}", file=sys.stderr)

        try:
            watcher.watch(on_event=_log_event)
        except KeyboardInterrupt:
            print("\n[cx] 监听已停止 (cancelled)", file=sys.stderr)
            return 130
        return 0

    if dst is None or not src.is_file():
        print(f"[cx] ERROR: 源文件不存在或非文件: {src}", file=sys.stderr)
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
            table=options.get("table", args.table),
            compression=options.get("compression", args.compression),
            on_progress=on_progress,
            on_error=options.get("on_error", args.on_error),
            output_rule=output_rule,
        )
    except ModuleNotFoundError as exc:
        missing = exc.name or str(exc)
        print(
            f"[cx] ERROR: 缺少可选依赖 '{missing}'。该格式需要额外安装："
            f"pip install {missing}（或 pip install omnicrawler[convertx-all]）",
            file=sys.stderr,
        )
        return 3
    except FileNotFoundError as exc:
        print(f"[cx] ERROR: 文件未找到: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[cx] ERROR: 参数/数据错误: {exc}", file=sys.stderr)
        return 4
    except (ConversionCancelledError, KeyboardInterrupt):
        print("\n[cx] 已中断 (cancelled)", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"[cx] ERROR: 转换失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    summary_payload = {
        "ok": str(result.status) != "skipped",
        "status": str(result.status),
        "source": str(src),
        "target": str(dst),
        "source_format": result.source_format,
        "target_format": result.target_format,
        "rows": result.rows,
        "written_records": result.extra.get("written_records"),
        "columns": result.columns,
        "warnings": result.warnings,
        "issues": [
            {"code": issue.code, "message": issue.message, "severity": issue.severity}
            for issue in result.issues
        ],
        "extra": result.extra or {},
    }
    if args.on_progress != "none":
        label = "SKIP" if str(result.status) == "skipped" else ("WARN" if result.warnings else "OK")
        print(f"[cx] {label}  " + json.dumps(summary_payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
