"""基因池维护服务（批 C-2）。

- ``import_scenes``：把场景 YAML（用户文件或 bundled 出厂默认）幂等导入 DB。
- ``run_maintenance``：淘汰低适应度基因、汇总统计。
- ``scene_report``：单场景体检（槽位数、候选验收、各槽位最优基因）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..state.scene_store import SceneStore


def import_scenes(store: SceneStore, *, path: str | None = None) -> dict[str, Any]:
    """导入场景定义。path 指定用户 YAML，否则导入包内 bundled 默认。"""
    if path:
        yaml_text = Path(path).expanduser().resolve().read_text(encoding="utf-8")
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


__all__ = ["import_scenes", "run_maintenance", "scene_report"]
