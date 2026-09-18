"""任务运行/恢复命令。"""

from __future__ import annotations

import sys
import time
from typing import Any

from ..core.config import load_config
from ..services.application_service import ApplicationService


def execute(
    config: str, command: str, *,
    max_pages: int | None = None,
    retry_failed: bool = False,
    progress: bool = False,
    strict: bool = False,
) -> dict[str, Any]:
    """运行或恢复采集任务。

    支持 --progress 标志启用实时进度输出到 stderr。
    完成后自动打印结构化摘要。

    S2.4.1：strict 模式下仅 succeeded 且有效记录 > 0 的退出码为 0，
    否则退出码为 1；默认（非 strict）保持向前兼容，成功即 0。
    """
    if progress:
        _last = [0, time.monotonic()]

        def _on_progress(event: str, details: dict[str, Any]) -> None:
            if event == "crawl_progress":
                current = details.get("processed", 0)
                # E7：管线进度事件发的是 "limit" 键（_run.py），兼容 "total"
                total = details.get("limit") or details.get("total") or max_pages or 0
                url = (details.get("url") or "")[:70]  # E8：去掉恒真的 "url" or "" 死代码
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
    # B02-026：CLI run 路径接入占位符门禁（fail-closed），与 GUI/worker 运行前检查对齐。
    # 整棵配置树扫描，覆盖 source.params / http.headers / pagination / max_pages 等全部字符串值。
    # 嵌入式/测试调用方若返回非标准配置对象（无 .raw），跳过检查不阻塞。
    raw_config = getattr(loaded, "raw", None)
    if isinstance(raw_config, dict):
        hits = _unresolved_placeholders(raw_config)
        if hits:
            print("❌ 配置中存在未替换的模板占位符，请先填充后再运行：", file=sys.stderr)
            for hit in hits:
                print(f"   - {hit}", file=sys.stderr)
            return {
                "status": "failed",
                "exit_code": 1,
                "records": 0,
                "errors": len(hits),
                "processed": 0,
                "elapsed_seconds": 0,
                "export": {},
                "workspace": str(getattr(loaded, "workspace", "")),
            }
    # E9：统一走 ApplicationService（内部透传 max_pages/callback 到 Pipeline.run），
    # 不再出现 max_pages 分支走 Pipeline、无 max_pages 分支走 ApplicationService 的双路径不一致。
    result = ApplicationService(loaded.path).run(
        resume=command == "resume",
        retry_failed=retry_failed,
        max_pages=max_pages,
        callback=callback,
    )

    if progress:
        print(file=sys.stderr)  # newline after progress bar

    status = result.get("status", "unknown")
    result["effective_records"] = int(result.get("records", 0))
    if status in {"failed", "cancelled"}:
        result["exit_code"] = 1
    elif strict:
        result["exit_code"] = (
            0 if status == "succeeded" and result["effective_records"] > 0 else 1
        )
    else:
        result["exit_code"] = 0

    _print_summary(result)
    if result["effective_records"] == 0:
        print(_zero_record_hint(result))
    return result


def _zero_record_hint(result: dict[str, Any]) -> str:
    """按**运行状态**给出 0 记录的原因分支（走查 R2.1）。

    旧实现是一段静态文案，把三条「可能原因」一股脑列出来。实测（0.13.0，2026-09-18 走查）：
    同一工作区复跑时真因是「内容未变化，被增量去重跳过」—— 三条里一条都不沾边，
    用户被引向错误方向（去查 403、去换模板），而真正的下一步是 `export` 重导。

    这里改为**先给可判定的判断，再给该分支下的下一步命令**；判断依据全部取自本次运行
    已有的事实（frontier 状态、已处理页数、错误数），不新增度量。
    """
    frontier = result.get("frontier")
    frontier = frontier if isinstance(frontier, dict) else {}
    blocked = int(frontier.get("blocked", 0) or 0)
    processed = int(result.get("processed", 0) or 0)
    records = int(result.get("records", 0) or 0)
    errors = int(result.get("errors", 0) or 0)

    head = "\n⚠ 本次任务有效记录为 0。"
    if blocked and not processed:
        body = (
            f"\n   判断：有 {blocked} 个地址被安全策略或 robots 拦住，页面从未被抓取。"
            "\n   下一步：omnicrawler security-report 查看拦截明细；"
            "omnicrawler doctor 检查出网与配置。"
        )
    elif processed and not records:
        body = (
            f"\n   判断：已成功处理 {processed} 个页面，但一条记录都没产出。"
            "\n     · 若是**第二次及以后**的运行：内容未变化时会被增量去重跳过，"
            "本次 0 条**不代表失败** —— 用 omnicrawler export --config <配置> 从断点库重新导出。"
            "\n     · 若是**首次**运行：多为选择器与页面结构不匹配 —— "
            "omnicrawler field-suggest 推荐字段，或 omnicrawler sample 小样本试跑。"
            "\n   仍未解决：omnicrawler doctor 检查环境与出网。"
        )
    elif not processed and not blocked:
        body = (
            "\n   判断：没有任何页面被处理 —— 请求根本没发出去"
            "（入口不可达 / DNS / 凭据 / 爬取预算为 0）。"
            "\n   下一步：omnicrawler doctor 检查环境与出网；"
            "omnicrawler preflight 做一次运行前检查。"
        )
    else:
        body = (
            "\n   判断：页面已抓取但未命中模板，且同时存在其他阻断因素。"
            "\n   下一步：omnicrawler doctor 检查环境；omnicrawler sample 小样本试跑验证模板。"
        )
    tail = ""
    if errors:
        tail = f"\n   另：本次记录到 {errors} 条错误，明细见工作区下的 error_center.html。"
    return head + body + tail


def _unresolved_placeholders(raw: dict[str, Any]) -> list[str]:
    """B02-026：扫描整棵配置树，返回含未替换占位符的字符串值（含键路径）。

    占位符形态复用 ``template_catalog.PLACEHOLDER_RE``（``{{identifier}}``），
    与模板声明同源，避免 ``{{ 任意文本 }}`` 注释性用法被误报。
    """
    from ..templates.template_catalog import PLACEHOLDER_RE

    hits: list[str] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            if PLACEHOLDER_RE.search(node):
                snippet = node[:80] + ("…" if len(node) > 80 else "")
                hits.append(f"{path}: {snippet}")
        elif isinstance(node, dict):
            for key, value in node.items():
                _walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                _walk(value, f"{path}[{index}]")

    _walk(raw, "")
    return hits


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
        lines.append("   下一步: omnicrawler export -c <配置>")

    print("\n".join(lines))
