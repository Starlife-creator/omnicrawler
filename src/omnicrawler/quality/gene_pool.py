"""选择器基因池（批 C-2）。

基因 = 一个「场景 × 槽位」下的候选选择器（css/regex/jsonpath），带
命中统计（hits/misses）与适应度（fitness）。基因池基于 SceneStore 的
``selector_genes`` 表，提供推荐、反馈、播种：

- ``recommend``：按适应度取最优基因（供抽取器优先尝试）。
- ``record``：一次使用反馈（命中/未命中）→ 更新计数并重算适应度。
- ``seed``：播种初始基因（来自场景 YAML 出厂默认，幂等）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..state.scene_store import SceneStore


@dataclass(frozen=True, slots=True)
class Gene:
    """一条选择器基因。"""

    scene: str
    slot_key: str
    selector: str
    selector_type: str = "css"
    fitness: float = 0.0
    hits: int = 0
    misses: int = 0
    enabled: bool = True


def fitness(hits: int, misses: int) -> float:
    """适应度 = 命中率；无任何尝试时为 0。"""
    total = hits + misses
    if total <= 0:
        return 0.0
    return round(hits / total, 4)


class GenePool:
    """基于 SceneStore.selector_genes 表的基因池逻辑。"""

    def __init__(self, store: SceneStore) -> None:
        self.store = store

    # ── 推荐 ───────────────────────────────────────────────
    def recommend(
        self,
        scene: str,
        slot_key: str,
        *,
        limit: int = 3,
        min_trials: int = 0,
    ) -> list[Gene]:
        """按适应度降序返回最优基因（min_trials>0 过滤冷启动基因）。

        高频调用路径：惰性触发 N5 基因维护（进程级 TTL 节流 + 膨胀阈值，
        未膨胀时仅一次 COUNT 开销，零 DELETE）。
        """
        try:
            from ..services.gene_maintenance import maybe_maintain

            maybe_maintain(self.store)
        except Exception:  # noqa: BLE001 — 维护失败不影响推荐
            pass
        rows = self.store.top_genes(
            scene, slot_key, limit=limit, min_trials=min_trials,
        )
        return [self._to_gene(row) for row in rows]

    # ── 反馈 ───────────────────────────────────────────────
    def record(self, scene: str, slot_key: str, selector: str, *, hit: bool) -> int:
        """记录一次基因使用结果（命中/未命中），返回基因 id。"""
        gene_id = self.store.upsert_gene(scene, slot_key, selector)
        self.store.record_gene_result(gene_id, hit=hit)
        return gene_id

    # ── 播种 ───────────────────────────────────────────────
    def seed(
        self,
        scene: str,
        slot_key: str,
        selector: str,
        *,
        selector_type: str = "css",
        parent_id: int | None = None,
    ) -> int:
        """播种/更新一条初始基因（幂等），返回基因 id。"""
        return self.store.upsert_gene(
            scene, slot_key, selector,
            selector_type=selector_type, parent_id=parent_id,
        )

    # ── 统计 ───────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        return self.store.gene_stats()

    def top_genes(self, scene: str) -> list[Gene]:
        rows = self.store.top_genes(scene, limit=50)
        return [self._to_gene(row) for row in rows]

    @staticmethod
    def _to_gene(row: dict[str, Any]) -> Gene:
        return Gene(
            scene=str(row["scene"]),
            slot_key=str(row["slot_key"]),
            selector=str(row["selector"]),
            selector_type=str(row.get("selector_type", "css")),
            fitness=float(row.get("fitness", 0.0)),
            hits=int(row.get("hits", 0)),
            misses=int(row.get("misses", 0)),
            enabled=bool(row.get("enabled", 1)),
        )


__all__ = ["Gene", "GenePool", "fitness"]
