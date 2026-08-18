from __future__ import annotations

import html
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.utils import atomic_write, utcnow
from ..state import StateStore
from .diagnostics import diagnose, redact_diagnostic_text


@dataclass(frozen=True, slots=True)
class ErrorDiagnosis:
    category: str
    severity: str
    retryable: bool
    explanation: str
    recommendation: str
    fix: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def diagnose_error(stage: str, error_type: str, message: str, retryable: bool = False) -> ErrorDiagnosis:
    """按业务化结构诊断错误。

    返回结构始终包含：
    - 发生了什么
    - 可能原因
    - 已经完成并保存了什么
    - 数据是否受到影响
    - 建议执行的操作
    - 是否可以安全重试
    """
    report = diagnose(f"{stage} {error_type} {message}", {"stage": stage, "error_type": error_type})
    retry = report.retryable or retryable
    severity = "warning" if retry else "error"
    action = report.auto_fix.command if report.auto_fix and report.auto_fix.command else "patch_config"
    patch = report.auto_fix.config_changes if report.auto_fix else {}
    fix = {
        "action": action,
        "patch": patch,
        "safe_to_retry": retry,
        "data_saved": report.data_impact,
        "fix_options": [report.action, "查看完整日志", "从检查点继续"],
    }
    return ErrorDiagnosis(
        report.category.value,
        severity,
        retry,
        explanation=report.cause,
        recommendation=report.action,
        fix=fix,
    )


def build_error_center(state: StateStore, output: Path, run_id: str | None = None) -> dict[str, Any]:
    where, params = (" WHERE run_id=?", (run_id,)) if run_id else ("", ())
    rows = state.rows(
        f"SELECT url, stage, error_type, message, retryable, created_at FROM errors{where} ORDER BY id",
        params,
    )
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    categories: Counter[str] = Counter()
    for row in rows:
        message = redact_diagnostic_text(str(row["message"]))
        diagnosis = diagnose_error(
            str(row["stage"]), str(row["error_type"]), message, bool(row["retryable"])
        )
        categories[diagnosis.category] += 1
        signature = (diagnosis.category, str(row["error_type"]), message[:300])
        group = grouped.setdefault(
            signature,
            {
                "category": diagnosis.category,
                "error_type": row["error_type"],
                "message": message,
                "count": 0,
                "sample_urls": [],
                "diagnosis": diagnosis.to_dict(),
            },
        )
        group["count"] += 1
        if row["url"] and len(group["sample_urls"]) < 5:
            group["sample_urls"].append(redact_diagnostic_text(str(row["url"])))
    report = {
        "run_id": run_id,
        "generated_at": utcnow(),
        "total_errors": len(rows),
        "unique_groups": len(grouped),
        "categories": dict(categories),
        "groups": sorted(grouped.values(), key=lambda item: (-item["count"], item["category"])),
    }
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "error_center.json"
    html_path = output / "error_center.html"
    atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    atomic_write(html_path, _html(report).encode("utf-8"))
    return {**report, "files": {"json": str(json_path), "html": str(html_path)}}


def _html(report: dict[str, Any]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item['category']))}</td><td>{item['count']}</td>"
        f"<td>{html.escape(str(item['message']))}</td>"
        f"<td>{html.escape(str(item['diagnosis']['recommendation']))}</td></tr>"
        for item in report["groups"]
    ) or '<tr><td colspan="4">本次运行没有错误</td></tr>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>错误中心</title>
<style>body{{font-family:system-ui,'Microsoft YaHei';background:#f6f8fb;color:#182033;padding:28px}}main{{max-width:1100px;margin:auto}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:12px;border-bottom:1px solid #e8ebf1;text-align:left;vertical-align:top}}th{{background:#eef3fb}}</style></head>
<body><main><h1>统一错误中心</h1><p>错误 {report['total_errors']} 条，合并为 {report['unique_groups']} 组。</p>
<table><thead><tr><th>类别</th><th>次数</th><th>原因</th><th>建议</th></tr></thead><tbody>{rows}</tbody></table></main></body></html>"""
