"""Read-only stage explanations bound to the saved task and trial identity."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from ..core.config import AppConfig
from ..templates.capture import config_digest


def describe(config: AppConfig, *, run_id: str = "") -> dict[str, Any]:
    source, extract = config.section("source"), config.section("extract")
    stages: list[dict[str, Any]] = []
    def add(identity: str, title: str, detail: str, check: str) -> None:
        stages.append({"id": identity, "title": title, "detail": detail, "check": check})
    add("ingest", "输入与范围", f"来源类型：{config.source_kind}；种子：{len(source.get('seeds', []))}", "核对访问域、页数和预算；运行计划不会访问网页。")
    if config.section("session").get("persist_cookies") or source.get("auth_check"):
        add("session", "登录会话", "使用本地会话引用；不展示 Cookie 或请求头。", "会话过期后停止、更新登录、重建执行资源，再恢复原任务。")
    if config.source_kind == "browser":
        for index, action in enumerate(config.section("browser").get("actions", []), 1):
            add(f"action-{index}", f"浏览器操作 {index}", f"动作：{action.get('action', 'unknown')}", "使用网页操作录制检查此步骤；输入值和凭据不显示在诊断中。")
    pagination = source.get("pagination") or {}
    if pagination:
        add("pagination", "分页与游标", f"分页：{pagination.get('type', 'cursor' if pagination.get('next_path') else '未启用')}", "试跑覆盖下一页；核对循环终止、页间重复及累计条目。")
    add("fetch", "抓取与详情发现", f"深度上限：{config.section('crawl').get('max_depth', 3)}；并发上限受资源档位约束。", "检查详情来源和升级原因；失败可在恢复页选择性重试。")
    add("extract", "字段提取与复核", f"提取模式：{extract.get('mode', 'auto')}；字段：{', '.join(extract.get('fields', {}))}", "用可视化选字段检查样例与证据；低置信字段在复核台处理。")
    if config.section("download").get("enabled"):
        add("download", "附件下载", "按声明扩展名下载；原文件与交付清单保留。", "核对链接、来源、文件哈希和累计成果；只重试所选失败附件。")
    if config.section("processors").get("pdf", {}).get("enabled"):
        add("pdf", "PDF 与 OCR", "使用任务声明的 PDF 处理设置。", "检查遗漏扫描页、字段置信度与原文页；不能把空文本当完整成果。")
    add("export", "交付与导出", "启用格式：" + ", ".join(key for key in ("jsonl", "csv", "xlsx") if config.section("outputs").get(key)), "重新打开交付文件核对字段、条目和累计结果。")
    trial: dict[str, Any] = {"state": "missing", "historical_reference_only": True}
    path = config.workspace / "preflight_acceptance.json"
    if path.is_file():
        try:
            with path.open("rb") as handle:
                payload = handle.read(1024**2 + 1)
            if len(payload) > 1024**2:
                raise ValueError("oversized trial")
            proof = json.loads(payload)
            matched = proof.get("config_sha256") == config_digest(config)
            complete = proof.get("summary", {}).get("status") == "succeeded" and bool(proof.get("samples"))
            trial.update(state="matching_history" if matched and complete else "stale_or_incomplete", captured_at=proof.get("captured_at", ""))
        except (ValueError, OSError, AttributeError):
            trial["state"] = "invalid"
    runtime = read_runtime(config, run_id=run_id)
    observed = {item["stage"]: item for item in runtime.get("stages", [])}
    for step in stages:
        item = observed.get(step["id"])
        step["runtime_status"] = item["status"] if item else "not_observed"
        step["runtime_detail"] = item or {}
    return {"status": "workflow_described", "stages": stages, "trial": trial, "runtime": runtime,
            "note": "阶段说明表示配置，不代表每一步已通过；历史试跑不批准新任务。"}


def read_runtime(config: AppConfig, *, run_id: str = "") -> dict[str, Any]:
    """Read one task's actual checkpoints without migration or network access."""
    database = config.workspace / "state.sqlite3"
    if not database.is_file():
        return {"state": "not_started", "stages": []}
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        has_identity = connection.execute("SELECT 1 FROM sqlite_master WHERE name='run_identities'").fetchone()
        task_id = str(config.section("project").get("task_id", ""))
        if task_id and has_identity:
            query = "SELECT r.* FROM runs r JOIN run_identities i ON i.run_id=r.run_id WHERE i.task_key=?"
            params = ["id:" + task_id]
        else:
            query = "SELECT r.* FROM runs r WHERE r.project_name=? AND r.config_path=?"
            params = [config.project_name, str(config.path)]
        if run_id:
            query += " AND r.run_id=?"
            params.append(run_id)
        query += " ORDER BY r.started_at DESC,r.rowid DESC LIMIT 1"
        row = connection.execute(query, params).fetchone()
        if row is None:
            if run_id:
                raise ValueError("运行不属于当前任务或不存在")
            return {"state": "not_started", "stages": []}
        identity = row["run_id"]
        checkpoints = connection.execute(
            "SELECT stage,status,COUNT(*) AS observations,MIN(updated_at) AS first_observed_at,MAX(updated_at) AS last_observed_at "
            "FROM stage_checkpoints WHERE run_id=? GROUP BY stage,status ORDER BY first_observed_at", (identity,)).fetchall()
        errors = connection.execute("SELECT stage,error_type,COUNT(*) AS total FROM errors WHERE run_id=? GROUP BY stage,error_type", (identity,)).fetchall()
        step_rows = connection.execute(
            "SELECT stage,status,payload_json FROM stage_checkpoints WHERE run_id=? AND idempotency_key LIKE 'step:%' "
            "ORDER BY updated_at,idempotency_key LIMIT 501", (identity,)).fetchall()
        steps = []
        for observed in step_rows[:500]:
            payload = json.loads(observed["payload_json"])
            allowed = {key: payload[key] for key in (
                "step_id", "parent_id", "started_at", "finished_at", "duration_seconds",
                "error_type", "records", "response_bytes", "http_status", "discovered", "rejected",
                "index", "attempt", "engine", "action", "omitted",
            ) if key in payload}
            steps.append({"stage": observed["stage"], "status": observed["status"], **allowed})
        grouped: dict[str, dict[str, Any]] = {}
        for checkpoint in checkpoints:
            stage = checkpoint["stage"]
            item = grouped.setdefault(stage, {"stage": stage, "status": checkpoint["status"], "observations": 0,
                                             "error_types": {}, "duration_seconds": None})
            item["observations"] += checkpoint["observations"]
            item["first_observed_at"] = checkpoint["first_observed_at"]
            item["last_observed_at"] = checkpoint["last_observed_at"]
            if checkpoint["status"] not in {"completed", "succeeded"}:
                item["status"] = checkpoint["status"]
        for error in errors:
            item = grouped.setdefault(error["stage"], {"stage": error["stage"], "observations": 0,
                                                       "error_types": {}, "duration_seconds": None})
            item["status"] = "failed"
            item["error_types"][error["error_type"]] = error["total"]
        for item in grouped.values():
            item["steps"] = [step for step in steps if step["stage"] == item["stage"]]
            item["steps_truncated"] = len(step_rows) > 500
        setup = connection.execute("SELECT payload_json FROM stage_checkpoints WHERE run_id=? AND stage='setup' AND idempotency_key='setup'", (identity,)).fetchone()
        digest = json.loads(setup[0]).get("config_sha256") if setup else None
        duration = None
        if row["finished_at"]:
            try:
                duration = (datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds()
            except ValueError:
                pass
        return {"state": "observed", "run_id": identity, "status": row["status"],
                "started_at": row["started_at"], "finished_at": row["finished_at"], "duration_seconds": duration,
                "config_match": "unknown" if not digest else "matching" if digest == config_digest(config) else "stale",
                "identity_confidence": ("legacy_config_path" if config.section("project").get("identity_origin") == "legacy_config_path"
                                        else "stable" if task_id and has_identity else "legacy_path"),
                "stages": list(grouped.values()),
                "steps": steps, "steps_truncated": len(step_rows) > 500,
                "note": "步骤起止为实际执行证据；并发步骤耗时不能相加作为总耗时。缺少起止证据的阶段耗时为未知。"}
    except sqlite3.Error:
        return {"state": "unavailable", "stages": [], "reason": "运行数据库暂不可读，请稍后重试"}
    finally:
        connection.close()
