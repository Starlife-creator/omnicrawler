"""N1：场景基因增强 — 提取后缺失字段用基因池最优选择器补提 + 反馈进化。

设计（收益最大化 / 风险最小化）：
- 默认关闭：无 ``scene`` 或 ``scene.sqlite3`` 不存在 → 零行为、零开销。
- 字段级去重：一页内每个缺失字段只补一次（``MAX_AUGMENT_FIELDS_PER_PAGE`` 硬上限），
  满足「基因进化只需要样本反馈」，避免 100 条记录 × N 字段的 lxml 查询风暴。
- 只补提第一个缺失该字段的记录，不做跨记录值复用（列表页同字段值可能不同，复用会填错）。
- 单节点 → str / 多节点 → list（自然对齐 list/标量槽位）；空 → 视为未命中不写回。
- 全程 try/except，任何失败仅告警，绝不阻断主流程（提取/保存/导出）。

首版范围：仅支持 css/xpath 基因对 HTML 补提；regex/jsonpath 基因留作后续
（避免模块职责爆炸，过度设计是大忌）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

MAX_AUGMENT_FIELDS_PER_PAGE = 30


def gene_augment_html(
    result: Any,
    records: list[Any],
    fields: dict[str, Any],
    scene: str,
    db_path: str | Path,
) -> dict[str, Any]:
    """对缺失字段做基因补提并反馈，返回统计；任何异常静默降级。

    Args:
        result: FetchResult（含原始 HTML）。
        records: ExtractedRecord 列表（带 .data 字典）。
        fields: extract.fields 配置（name -> rule dict）。
        scene: 场景 ID（GenePool.recommend/record 的场景键）。
        db_path: scene.sqlite3 路径。

    Returns:
        ``{"active", "augmented", "hit", "miss", "skipped_no_gene"}`` 统计。
    """
    stats = {"active": False, "augmented": 0, "hit": 0, "miss": 0, "skipped_no_gene": 0}
    if not scene or not fields or not records:
        return stats
    db = Path(db_path)
    if not db.exists():
        return stats
    try:
        from lxml import html as lxml_html  # 可选依赖：缺失时静默跳过
    except Exception:  # noqa: BLE001
        return stats
    stats["active"] = True

    # 1. 收集缺失字段（字段级去重；只记录第一个缺失该字段的 record 作补提样本）
    missing: dict[str, Any] = {}
    for record in records:
        data = getattr(record, "data", None) or {}
        if not isinstance(data, dict):
            continue
        for name, rule in fields.items():
            if not isinstance(rule, dict) or name in missing:
                continue
            value = data.get(str(name))
            if value in (None, "", []):
                missing[name] = record
                if len(missing) >= MAX_AUGMENT_FIELDS_PER_PAGE:
                    break
        if len(missing) >= MAX_AUGMENT_FIELDS_PER_PAGE:
            break
    if not missing:
        return stats

    # 2. 解析 HTML（失败则整体跳过）
    try:
        from ..extraction.extractors import decode_body

        document = lxml_html.fromstring(decode_body(result))
    except Exception:  # noqa: BLE001
        LOGGER.warning("基因增强：HTML 解析失败", exc_info=True)
        return stats

    # 3. 逐字段补提 + 反馈（任何异常仅告警）
    try:
        from ..quality.gene_pool import GenePool
        from ..state.scene_store import SceneStore

        with SceneStore(db) as store:
            pool = GenePool(store)
            for field, record in missing.items():
                # min_trials=0：让 YAML 播种的冷启动基因也参与补提，
                # 否则 0 尝试基因被过滤，闭环永远无法启动（无反馈可积累）
                genes = pool.recommend(scene, field, limit=1, min_trials=0)
                if not genes:
                    stats["skipped_no_gene"] += 1
                    continue
                gene = genes[0]
                value = _extract_with_selector(document, gene.selector, gene.selector_type)
                if value in (None, "", []):
                    pool.record(scene, field, gene.selector, hit=False)
                    stats["miss"] += 1
                    continue
                data = getattr(record, "data", None)
                if isinstance(data, dict):
                    data[field] = value
                pool.record(scene, field, gene.selector, hit=True)
                stats["hit"] += 1
                stats["augmented"] += 1
    except Exception:  # noqa: BLE001 — 基因增强失败绝不阻断主流程
        LOGGER.warning("基因增强失败: %s", exc_info=True)
    return stats


def _extract_with_selector(document: Any, selector: str, selector_type: str) -> Any:
    """在已解析 HTML 上按基因选择器提取；单节点→str，多节点→list，空→None。"""
    if not selector:
        return None
    try:
        if selector_type == "xpath":
            nodes = document.xpath(selector)
        else:
            nodes = document.cssselect(selector)
    except Exception:  # noqa: BLE001
        return None
    if not nodes:
        return None
    values: list[str] = []
    for node in nodes:
        text = " ".join(str(node.text_content() or "").split())
        if text:
            values.append(text)
    if not values:
        return None
    return values[0] if len(values) == 1 else values


__all__ = ["MAX_AUGMENT_FIELDS_PER_PAGE", "gene_augment_html"]
