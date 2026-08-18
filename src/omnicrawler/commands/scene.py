"""场景/槽位/基因管理 CLI 后端（批 C 收尾）。

动作：
- import      —— 导入场景定义（缺省 bundled 出厂默认；--path 指定用户 YAML）
- list        —— 列出全部场景（槽位数 / 基因数）
- show        —— 单场景体检（槽位、候选验收、各槽位最优基因）
- candidates  —— 列出抽取候选（可筛选场景 / 验收状态）
- accept      —— 验收指定候选（accepted=1）
- maintenance —— 淘汰低适应度基因（**删除操作**：缺省只预览，--apply 才执行）

DB 单一真源：``<workspace>/scene.sqlite3``（与 observation.sqlite3 同目录约定）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import load_config
from ..state.scene_store import SceneStore


def _db_path(config: str) -> Path:
    return load_config(config).workspace / "scene.sqlite3"


def execute(
    action: str,
    *,
    config: str,
    scene: str = "",
    path: str = "",
    candidate_id: int = 0,
    limit: int = 100,
    pending_only: bool = False,
    accepted_only: bool = False,
    min_fitness: float = 0.2,
    min_trials: int = 3,
    apply: bool = False,
) -> dict[str, Any]:
    """执行场景管理动作，返回结构化结果（供 CLI _json 输出）。"""
    from ..services.gene_maintenance import import_scenes, scene_report

    db = _db_path(config)
    with SceneStore(db) as store:
        if action == "import":
            return {"db": str(db), **import_scenes(store, path=path or None)}
        if action == "list":
            return {"db": str(db), "scenes": store.list_scenes()}
        if action == "show":
            if not scene:
                raise ValueError("scene show 需要场景名（位置参数）")
            return {"db": str(db), **scene_report(store, scene)}
        if action == "candidates":
            accepted = None
            if pending_only:
                accepted = False
            elif accepted_only:
                accepted = True
            return {
                "db": str(db),
                "scene": scene,
                "candidates": store.candidates(
                    scene=scene or None, accepted=accepted, limit=limit,
                ),
            }
        if action == "accept":
            if candidate_id <= 0:
                raise ValueError("scene accept 需要候选 ID（位置参数）")
            store.accept_candidate(candidate_id)
            return {"db": str(db), "accepted": candidate_id}
        if action == "maintenance":
            if not apply:
                # 删除操作干跑：只预览将淘汰的基因，不执行 DELETE
                doomed = store.prune_candidates(
                    scene or None, min_fitness=min_fitness, min_trials=min_trials,
                )
                return {
                    "db": str(db),
                    "dry_run": True,
                    "will_prune": len(doomed),
                    "candidates": doomed,
                    "hint": "删除操作，请加 --apply 确认后执行",
                    **store.gene_stats(),
                }
            pruned = store.prune_genes(
                scene or None, min_fitness=min_fitness, min_trials=min_trials,
            )
            return {"db": str(db), "pruned": pruned, **store.gene_stats()}
        raise ValueError(f"未知场景操作: {action}")


__all__ = ["execute"]
