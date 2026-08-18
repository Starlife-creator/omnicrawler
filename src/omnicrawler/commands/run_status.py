"""运行状态查询命令。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import load_config
from ..state import StateStore


def execute(config: str, *, output_format: str = "json") -> dict[str, Any]:
    loaded = load_config(config)
    database = loaded.workspace / "state.sqlite3"
    result: dict[str, Any]
    if not database.exists():
        result = {"crawl": {"status": "not_started", "database": str(database)}}
    else:
        with StateStore(database) as state:
            result = {"crawl": {"latest_run": state.latest_run(), "totals": state.stats()}}
    # E3：配置路径由 execute 显式携带，避免 _print_text 永远打印空路径
    result["config_path"] = str(Path(config).expanduser().resolve())
    pdf_settings = loaded.section("processors").get("pdf", {})
    configured = str(pdf_settings.get("project_config", "")).strip()
    pdf_project = (
        loaded.resolve(configured)
        if configured
        else loaded.workspace / "pdf" / "project.yaml"
    )
    if pdf_project.is_file():
        from ..pdfx.config import load_config as load_pdf_config
        from ..pdfx.database import Database
        from ..pdfx.service import database_status

        pdf_config = load_pdf_config(pdf_project)
        if pdf_config.database.is_file():
            with Database(pdf_config.database) as pdf_database:
                result["pdf"] = {
                    "project_config": str(pdf_project),
                    **database_status(pdf_database),
                }
        else:
            result["pdf"] = {"status": "not_started", "project_config": str(pdf_project)}
    else:
        result["pdf"] = {"status": "not_configured"}

    if output_format == "text":
        _print_text(result)
    return result


def _print_text(result: dict[str, Any]) -> None:
    """以人类可读格式输出状态到 stdout。"""
    crawl = result.get("crawl", {})
    latest = crawl.get("latest_run") or {}
    totals = crawl.get("totals", {})

    print("═══ 采集状态 ═══")
    status = latest.get("status") or crawl.get("status") or "unknown"
    icons = {"succeeded": "✅", "running": "🔄", "failed": "❌", "cancelled": "⏹", "not_started": "⏳"}
    print(f"  状态:      {icons.get(status, '·')} {status}")
    if latest.get("started_at"):
        print(f"  开始时间:  {latest['started_at']}")
    if latest.get("finished_at"):
        print(f"  结束时间:  {latest['finished_at']}")
    if latest.get("project_name"):
        print(f"  项目名称:  {latest['project_name']}")

    if totals:
        print()
        print("═══ 统计 ═══")
        pending = totals.get("pending", 0)
        done = totals.get("done", 0)
        failed = totals.get("failed", 0)
        in_progress = totals.get("in_progress", 0)
        total = pending + done + failed + in_progress
        if total:
            pct = done * 100 // total if total else 0
            bar_width = 20
            filled = int(bar_width * done / total)
            bar = "█" * filled + "░" * (bar_width - filled)
            print(f"  进度:      [{bar}] {done}/{total} ({pct}%)")
        print(f"  已完成:    {done}")
        print(f"  进行中:    {in_progress}")
        print(f"  待处理:    {pending}")
        print(f"  失败:      {failed}")
        records = totals.get("records", 0)
        if records:
            print(f"  提取记录:  {records}")
        artifacts = totals.get("artifacts", 0)
        if artifacts:
            print(f"  附件:      {artifacts}")

    pdf = result.get("pdf", {})
    if pdf:
        print()
        print("═══ PDF 子项目 ═══")
        pdf_status = pdf.get("status", "unknown")
        print(f"  状态:      {pdf_status}")
        if pdf.get("project_config"):
            print(f"  配置:      {pdf['project_config']}")

    db_path = crawl.get("database", "")
    if db_path:
        print(f"\n💾 数据库: {db_path}")
    # E3：配置路径由 execute 携带（不再是永不写入的 _config_path）
    print(f"   配置:    {result.get('config_path', '')}")
