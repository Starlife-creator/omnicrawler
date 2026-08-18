"""证据胶囊 CLI 后端（批 B-1）：timeline 查看 + replay 限定重放。

- timeline：无 --run 时聚合各 run 胶囊统计；指定 --run 时按时间顺序列出
  该 run 的动作时间线（含输入规则摘要与输出值/置信度）。
- replay：基于胶囊（输入规则 + dom_hash）与归档 raw（responses.raw_path）
  重放字段提取，结果结构化输出（ok/no_capsule/archive_missing/dom_changed/
  timeout/error）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import load_config
from ..state.capsule_store import CapsuleStore


def timeline(
    config: str,
    *,
    run_id: str = "",
    capsule_dir: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """查看胶囊时间线。run_id 为空时返回各 run 统计目录。"""
    store = CapsuleStore(_resolve_dir(config, capsule_dir))
    if run_id:
        return _timeline_run(store, run_id, limit)
    return _timeline_catalog(store)


def _timeline_catalog(store: CapsuleStore) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for path in sorted(store.base_dir.glob("*.log")):
        capsules = store.read(path.stem)
        if not capsules:
            continue
        runs.append({
            "run_id": path.stem,
            "count": len(capsules),
            "first_at": capsules[0].timestamp,
            "last_at": capsules[-1].timestamp,
        })
    return {"capsule_dir": str(store.base_dir), "runs": runs, "total": len(runs)}


def _timeline_run(store: CapsuleStore, run_id: str, limit: int) -> dict[str, Any]:
    capsules = store.read(run_id)
    capsules.sort(key=lambda capsule: capsule.timestamp)  # 按时间顺序（追加序即时间序）
    events: list[dict[str, Any]] = []
    for index, capsule in enumerate(capsules, 1):
        if limit and index > limit:
            break
        input_data = capsule.input if isinstance(capsule.input, dict) else {}
        output = capsule.output if isinstance(capsule.output, dict) else {}
        rule = input_data.get("rule")
        rule_hint = rule if isinstance(rule, str) else str(
            (rule or {}).get("selector", (rule or {}).get("path", ""))
        )
        trace = output.get("trace")
        events.append({
            "index": index,
            "timestamp": capsule.timestamp,
            "action": f"{capsule.action_type}:{capsule.action_name}",
            "parent_id": capsule.parent_id,
            "url": input_data.get("url"),
            "rule": rule_hint,
            "value": output.get("value"),
            "dom_hash": str(output.get("dom_hash", ""))[:12],
            "confidence": trace.get("confidence") if isinstance(trace, dict) else None,
        })
    return {
        "run_id": run_id,
        "capsule_dir": str(store.base_dir),
        "count": len(capsules),
        "events": events,
        "truncated": len(capsules) > limit,
    }


def replay(
    config: str,
    *,
    run_id: str,
    field: str,
    stage: str = "extract",
    capsule_dir: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """基于胶囊 + 归档 raw 限定重放字段提取。"""
    from ..services.replay import replay_field
    from ..state import StateStore

    workspace = load_config(config).workspace
    base_dir = Path(capsule_dir).expanduser().resolve() if capsule_dir else workspace / "capsules"
    with StateStore(workspace / "state.sqlite3") as state:
        return replay_field(
            run_id, field, stage=stage, store=state,
            capsule_dir=base_dir, timeout=timeout,
        )


def _resolve_dir(config: str, capsule_dir: str | None) -> Path:
    """胶囊目录：显式指定优先，否则取 workspace/capsules（与埋点默认一致）。"""
    if capsule_dir:
        return Path(capsule_dir).expanduser().resolve()
    return load_config(config).workspace / "capsules"


__all__ = ["replay", "timeline"]
