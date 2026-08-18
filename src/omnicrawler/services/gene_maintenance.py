"""基因池维护服务（批 C-2）。

- ``import_scenes``：把场景 YAML（用户文件或 bundled 出厂默认）幂等导入 DB。
- ``run_maintenance``：淘汰低适应度基因、汇总统计。
- ``maybe_maintain``：惰性维护（进程级 TTL 节流 + 膨胀阈值），供高频调用路径接入。
- ``scene_report``：单场景体检（槽位数、候选验收、各槽位最优基因）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..security.paths import require_workspace_path
from ..state.scene_store import SceneStore

LOGGER = logging.getLogger(__name__)

# N5：进程级维护节流（避免 GenePool.recommend 高频调用触发 COUNT 风暴）
_MAINT_LOCK = threading.Lock()
_LAST_CHECK = 0.0


def import_scenes(store: SceneStore, *, path: str | None = None) -> dict[str, Any]:
    """导入场景定义。path 指定用户 YAML，否则导入包内 bundled 默认。

    B08-008：path 是外部可控路径，必须位于 scene 库所在工作区内
    （store._path.parent = config.workspace），防越界读取工作区外文件。
    """
    if path:
        yaml_text = require_workspace_path(
            path, root=store._path.parent, what="场景导入 YAML"
        ).read_text(encoding="utf-8")
        return store.import_scene_yaml(yaml_text)
    return store.import_bundled_scenes()


def run_maintenance(
    store: SceneStore,
    *,
    scene: str | None = None,
    min_fitness: float = 0.2,
    min_trials: int = 3,
) -> dict[str, Any]:
    """淘汰尝试次数达标但适应度过低的基因，返回维护统计。

    min_fitness / min_trials 语义：仅当基因尝试次数 ≥ min_trials 且
    适应度 < min_fitness 才淘汰（保护冷启动基因不被误杀）。
    """
    pruned = store.prune_genes(
        scene, min_fitness=min_fitness, min_trials=min_trials,
    )
    return {"pruned": pruned, **store.gene_stats()}


def maybe_maintain(
    store: SceneStore,
    *,
    max_genes: int = 5000,
    min_fitness: float = 0.2,
    min_trials: int = 3,
    ttl_seconds: float = 300.0,
) -> bool:
    """惰性维护：基因池膨胀到阈值才淘汰低适应度基因。

    设计（避免 COUNT 风暴 / 避免在未膨胀时做无用清理）：
    - 进程级 TTL 节流：TTL 内无论调用多少次，只检查一次 total。
    - 膨胀阈值：``total < max_genes`` 时零 DELETE（仅一次 COUNT）。
    - 任何异常静默降级，不影响调用方。

    Returns:
        ``True`` 表示本次执行了维护；否则 ``False``。
    """
    global _LAST_CHECK
    now = time.monotonic()
    with _MAINT_LOCK:
        if now - _LAST_CHECK < ttl_seconds:
            return False
        _LAST_CHECK = now
    try:
        if store.gene_stats()["total"] < max_genes:
            return False
        store.prune_genes(min_fitness=min_fitness, min_trials=min_trials)
        return True
    except Exception:  # noqa: BLE001 — 维护失败不阻断调用方
        LOGGER.warning("基因维护失败", exc_info=True)
        return False


def scene_report(store: SceneStore, scene: str) -> dict[str, Any]:
    """单场景体检报告：槽位、候选验收情况、各槽位最优基因。"""
    slots = store.get_slots(scene)
    candidates = store.candidates(scene=scene, limit=500)
    accepted = sum(1 for item in candidates if item["accepted"])
    slot_genes: dict[str, list[dict[str, Any]]] = {}
    for slot in slots:
        genes = store.top_genes(scene, slot.slot_key, limit=1, min_trials=1)
        slot_genes[slot.slot_key] = [
            {
                "selector": row["selector"],
                "selector_type": row["selector_type"],
                "fitness": row["fitness"],
                "hits": row["hits"],
                "misses": row["misses"],
            }
            for row in genes
        ]
    return {
        "scene": scene,
        "slots": [slot.slot_key for slot in slots],
        "candidates": len(candidates),
        "accepted": accepted,
        "slot_genes": slot_genes,
    }


__all__ = ["import_scenes", "maybe_maintain", "run_maintenance", "scene_report"]
