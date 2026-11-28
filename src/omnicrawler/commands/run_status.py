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
            latest = state.latest_run()
            result = {"crawl": {"latest_run": latest, "totals": state.stats()}}
            # ★ 2026-11-28：失败的运行要给出**可操作的诊断**，而不只是状态字符串。
            #
            #   此前「为什么失败」只能靠用户自己翻日志 —— 而
            #   `quality.diagnostics.diagnose_from_state()` 明明写好了分类规则
            #   （网络瞬断 / 限流 / 选择器失效 / 登录失效 / 浏览器不可用…），
            #   却**零调用方**，而且内部调了一个不存在的方法（见该函数注释），
            #   修好后这里才有意义。
            #
            #   只在**确实有失败**时才做：成功运行的诊断结果是空列表，
            #   平白多一次查库不值得。
            run_id = str((latest or {}).get("run_id") or "").strip()
            totals = result["crawl"].get("totals") or {}
            # ★ 判据用**真实存在的键**：`stats()` 给的是
            #   `frontier{status:count}` / `responses` / `records` / `errors` / …
            #   —— 没有 `failed` 这个平铺键。我第一版按 `totals["failed"]` 判断，
            #   条件恒为假（`.get` 返回 None），诊断段永远不会出现。
            #
            #   失败有两种来源，都要算：
            #   · `errors` 表有记录（`add_error` 写入的异常明细）；
            #   · `frontier` 里有 `failed` 状态（请求级失败）。
            frontier = totals.get("frontier") or {}
            failed_count = int(frontier.get("failed") or 0)
            error_count = int(totals.get("errors") or 0)
            if run_id and (failed_count > 0 or error_count > 0):
                from ..quality.diagnostics import diagnose_from_state

                reports = diagnose_from_state(run_id, state)
                if reports:
                    result["diagnostics"] = [
                        {
                            "category": item.category.value,
                            "cause": item.cause,
                            "action": item.action,
                            "data_impact": item.data_impact,
                            "retryable": item.retryable,
                            "help_id": item.help_id,
                        }
                        for item in reports
                    ]
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
        # 迁移后（2026-11-17）：PDF 工作台已迁成插件 document-workbench，
        # 宿主侧不再有 pdfx。状态查询改为经 facade 转给插件。
        #
        # 注意这里**不能抛异常**：`status` 是诊断命令，用户正是用它来查
        # "为什么没跑起来"。插件没装时给出 `plugin_not_installed` 比抛栈有用得多。
        from ..services import document_facade

        if not document_facade.document_capability()["available"]:
            result["pdf"] = {
                "status": "plugin_not_installed",
                "project_config": str(pdf_project),
                "hint": "文档工作台插件未安装（OmniCrawler 市场 → document-workbench）",
            }
        else:
            outcome = document_facade.run_operation(
                "pdf.status", {"config_path": str(pdf_project)}
            )
            if outcome.get("ok"):
                result["pdf"] = {
                    "project_config": str(pdf_project),
                    **dict(outcome.get("status") or {}),
                }
            else:
                result["pdf"] = {
                    "status": "unavailable",
                    "project_config": str(pdf_project),
                    "error": dict(outcome.get("error") or {}),
                }
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
