from __future__ import annotations

import html
import json
from typing import Any

from ..core.config import AppConfig
from ..core.utils import atomic_write, utcnow
from ..security.security_audit import pii_summary
from ..state import StateStore


def build_quality_report(config: AppConfig, state: StateStore, run_id: str | None) -> dict[str, Any]:
    where, params = (" WHERE run_id=?", (run_id,)) if run_id else ("", ())
    rows = state.rows(f"SELECT data_json, evidence_json FROM records{where}", params)
    qualities: list[dict[str, Any]] = []
    resolved_entities = 0
    for row in rows:
        evidence = json.loads(row["evidence_json"])
        quality = evidence.get("_quality", {})
        if isinstance(quality, dict):
            qualities.append(quality)
        resolutions = evidence.get("_entity_resolution", [])
        resolved_entities += len(resolutions) if isinstance(resolutions, list) else 0
    changes = state.rows(
        f"SELECT change_type, COUNT(*) AS count FROM semantic_changes{where} GROUP BY change_type",
        params,
    )
    fields = state.quality_stats(run_id) if run_id else []
    total = len(rows)
    review = sum(bool(item.get("review_required")) for item in qualities)
    near_duplicates = sum(bool(item.get("near_duplicate")) for item in qualities)
    average_score = sum(float(item.get("score", 0)) for item in qualities) / max(1, len(qualities))
    report: dict[str, Any] = {
        "project": config.project_name,
        "run_id": run_id,
        "generated_at": utcnow(),
        "records": total,
        "average_quality_score": round(average_score, 4),
        "review_required": review,
        "near_duplicates": near_duplicates,
        "entities_resolved": resolved_entities,
        "semantic_changes": {row["change_type"]: row["count"] for row in changes},
        "fields": fields,
        "pii_candidates": pii_summary([json.loads(row["data_json"]) for row in rows]),
    }
    output = config.workspace / "output"
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "quality_report.json"
    html_path = output / "quality_report.html"
    atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    atomic_write(html_path, _render_html(report).encode("utf-8"))
    report["files"] = {"json": str(json_path), "html": str(html_path)}
    return report


def _render_html(report: dict[str, Any]) -> str:
    fields = report.get("fields", [])
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('field_name', '')))}</td>"
        f"<td>{item.get('present', 0)}/{item.get('total', 0)}</td>"
        f"<td>{item.get('valid', 0)}</td><td>{item.get('anomalies', 0)}</td></tr>"
        for item in fields
    ) or '<tr><td colspan="4">暂无字段统计</td></tr>'
    cards = (
        ("记录", report["records"]),
        ("平均质量", f"{report['average_quality_score']:.1%}"),
        ("需复核", report["review_required"]),
        ("近似重复", report["near_duplicates"]),
        ("实体归一", report["entities_resolved"]),
    )
    card_html = "".join(
        f'<div class="card"><span>{html.escape(str(label))}</span><strong>{html.escape(str(value))}</strong></div>'
        for label, value in cards
    )
    changes = html.escape(json.dumps(report.get("semantic_changes", {}), ensure_ascii=False))
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>OmniCrawler 数据质量报告</title>
<style>body{{font-family:system-ui,'Microsoft YaHei',sans-serif;background:#f5f7fb;color:#172033;margin:0;padding:32px}}
.wrap{{max-width:1040px;margin:auto}}h1{{margin-bottom:6px}}.sub{{color:#687386}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}}
.card,section{{background:white;border:1px solid #e5e9f1;border-radius:12px;padding:18px;box-shadow:0 3px 14px #25324a0d}}.card span{{display:block;color:#687386}}.card strong{{font-size:28px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #edf0f5;text-align:left}}code{{white-space:pre-wrap}}</style></head>
<body><div class="wrap"><h1>数据质量报告</h1><div class="sub">{html.escape(str(report['project']))} · {html.escape(str(report.get('run_id') or '全部运行'))}</div>
<div class="cards">{card_html}</div><section><h2>字段健康度</h2><table><thead><tr><th>字段</th><th>完整度</th><th>有效</th><th>异常</th></tr></thead><tbody>{rows}</tbody></table></section>
<section style="margin-top:12px"><h2>语义变化</h2><code>{changes}</code></section></div></body></html>"""
