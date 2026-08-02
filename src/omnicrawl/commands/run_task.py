"""任务运行/恢复命令。"""

from __future__ import annotations

import sys
import time
from typing import Any

from ..core.config import load_config
from ..pipeline import Pipeline
from ..services.application_service import ApplicationService


def execute(
    config: str, command: str, *,
    max_pages: int | None = None,
    retry_failed: bool = False,
    progress: bool = False,
) -> dict[str, Any]:
    """运行或恢复采集任务。

    支持 --progress 标志启用实时进度输出到 stderr。
    完成后自动打印结构化摘要。
    """
    if progress:
        _last = [0, time.monotonic()]

        def _on_progress(event: str, details: dict[str, Any]) -> None:
            if event == "crawl_progress":
                current = details.get("processed", 0)
                total = details.get("total", 0) or max_pages or 0
                url = (details.get("url" or "") or "")[:70]
                bar_width = 30
                if total:
                    filled = int(bar_width * current / total) if total else 0
                    bar = "█" * filled + "░" * (bar_width - filled)
                    pct = current * 100 // total if total else 0
                else:
                    bar = "?" * bar_width
                    pct = 0
                elapsed = time.monotonic() - _last[1]
                if current != _last[0]:
                    rate = (current - _last[0]) / max(elapsed, 0.1)
                    eta = (total - current) / max(rate, 0.01) if total else 0
                    eta_str = f"{eta:.0f}s" if eta < 120 else f"{eta/60:.1f}min"
                else:
                    eta_str = "---"
                _last[0] = current
                _last[1] = time.monotonic()
                print(
                    f"\r  [{bar}] {current}/{total} ({pct}%)  ETA {eta_str}  {url}",
                    end="", file=sys.stderr, flush=True,
                )

        callback = _on_progress
    else:
        callback = None

    loaded = load_config(config)
    if max_pages is not None:
        loaded.raw["crawl"]["max_pages"] = max_pages
        with Pipeline(loaded) as pipeline:
            result = pipeline.run(
                resume=command == "resume",
                retry_failed=retry_failed,
                max_pages=max_pages,
                callback=callback,
            )
    else:
        result = ApplicationService(config).run(
            resume=command == "resume",
            retry_failed=retry_failed,
        )

    if progress:
        print(file=sys.stderr)  # newline after progress bar

    _print_summary(result)
    return result


def _print_summary(result: dict[str, Any]) -> None:
    """打印人类可读的任务完成摘要。"""
    status = result.get("status", "unknown")
    status_icon = {"succeeded": "✅", "cancelled": "⏹", "failed": "❌"}.get(status, "⚠")
    processed = result.get("processed", 0)
    records = result.get("records", 0)
    artifacts = result.get("artifacts", 0)
    elapsed = result.get("elapsed_seconds", 0)

    lines = [f"\n{status_icon} 任务 {status}"]
    if processed:
        lines.append(f"   采集页面: {processed}")
    if records:
        lines.append(f"   提取记录: {records}")
    if artifacts:
        lines.append(f"   下载附件: {artifacts}")
    if elapsed:
        lines.append(f"   耗时: {elapsed:.1f}s")
    errors = result.get("errors", 0)
    if errors:
        lines.append(f"   ⚠ 错误: {errors}")

    export = result.get("export", {})
    if isinstance(export, dict):
        for fmt, path in export.items():
            if isinstance(path, str) and path:
                lines.append(f"   📄 {fmt}: {path}")

    workspace = result.get("workspace", "")
    if workspace:
        lines.append(f"\n   输出目录: {workspace}/output/")
        lines.append("   下一步: omnicrawl export -c <配置>")

    print("\n".join(lines))
