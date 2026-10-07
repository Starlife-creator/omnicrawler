"""Fresh local starter and evidence-bound next steps for a saved task."""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

from ..core.config import AppConfig, load_config
from .workflow_diagnostics import describe


def create_starter(parent: Path) -> Path:
    parent = parent.expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="starter-", dir=parent))
    data = root / "items.json"
    data.write_text(json.dumps({"items": [{"name": "苹果", "price": 0, "in_stock": False},
                                          {"name": "香蕉", "price": 22, "in_stock": True},
                                          {"name": "樱桃", "price": 33, "in_stock": True}]}, ensure_ascii=False), encoding="utf8")
    config = root / "starter.yaml"
    raw = {"project": {"name": "离线入门", "task_id": uuid.uuid4().hex, "workspace": "work"},
           "source": {"kind": "file", "local_files": [data.name], "seeds": [data.as_uri()]}, "crawl": {"max_pages": 1, "concurrency": 1},
           "extract": {"mode": "json", "item_path": "$.items[*]", "fields": {
               "name": {"path": "name"}, "price": {"path": "price"}, "in_stock": {"path": "in_stock"}}},
           "ai": {"mode": "disabled"}, "download": {"enabled": False}, "processors": {"pdf": {"enabled": False}},
           "outputs": {"jsonl": True, "csv": True, "xlsx": True}}
    config.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf8")
    load_config(config)
    return config


def progress(config: AppConfig) -> dict[str, Any]:
    observed = describe(config)
    runtime, trial = observed["runtime"], observed["trial"]
    trial_state = "passed" if trial["state"] == "matching_history" else "stale" if trial["state"] == "stale_or_incomplete" else "pending"
    run_state = "passed" if runtime.get("status") == "succeeded" and runtime.get("config_match") == "matching" else "stale" if runtime.get("config_match") == "stale" else "pending"
    steps = [
        {"step": "fields", "state": "passed" if config.section("extract").get("fields") else "pending", "action": "在工作台检查来源、范围、字段与交付设置，并保存配置。"},
        {"step": "trial", "state": trial_state, "action": "点击小样本试跑，核对字段样例；修改规则后重新试跑。"},
        {"step": "run", "state": run_state, "action": "试跑通过后开始全量运行；失败时按诊断修复或登录后恢复。"},
        {"step": "delivery", "state": "awaiting_user_review" if run_state == "passed" else "pending", "action": "到结果页核对记录，再打开 CSV / Excel；仅运行成功不代表人工核对完成。"},
    ]
    next_step = next(item for item in steps if item["state"] != "passed")
    return {"config": str(config.path), "steps": steps, "next_step": next_step, "output_directory": str(config.workspace / "output"),
            "run_id": runtime.get("run_id"), "note": "进度依据当前配置对应的持久化证据；交付人工核对不自动标为完成。"}
