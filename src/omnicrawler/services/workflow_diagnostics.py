"""Read-only stage explanations bound to the saved task and trial identity."""
from __future__ import annotations

import json
from typing import Any

from ..core.config import AppConfig
from ..templates.capture import config_digest


def describe(config: AppConfig) -> dict[str, Any]:
    source, extract = config.section("source"), config.section("extract")
    stages = []
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
    return {"status": "workflow_described", "stages": stages, "trial": trial,
            "note": "阶段说明表示配置，不代表每一步已通过；历史试跑不批准新任务。"}
