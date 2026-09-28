"""N1：场景基因增强 — 提取后缺失字段用基因池最优选择器补提 + 反馈进化。

设计（收益最大化 / 风险最小化）：
- 默认关闭：无 ``scene`` 或 ``scene.sqlite3`` 不存在 → 零行为、零开销。
- 字段级去重：一页内每个缺失字段只补一次（``MAX_AUGMENT_FIELDS_PER_PAGE`` 硬上限），
  满足「基因进化只需要样本反馈」，避免 100 条记录 × N 字段的查询风暴。
- 只补提第一个缺失该字段的记录，不做跨记录值复用（列表页同字段值可能不同，复用会填错）。
- 单节点 → str / 多节点 → list（自然对齐 list/标量槽位）；空 → 视为未命中不写回。
- 全程 try/except，任何失败仅告警，绝不阻断主流程（提取/保存/导出）。

选择器类型：css / xpath 沿用本模块原有的 lxml 抽取（保留「多节点 → list」
的既有语义）；regex / jsonpath / text 转交 ``doc_extractors``，不自建抽取
逻辑。理由是 doc_extractors 已经把这三种类型实现好了，且各自带上了本模块
不需要重造的东西：

- ``regex`` 走 ``_regex_value`` → ``safe_regex_search``，带病态正则防护
- ``jsonpath`` 走 ``core.jsonpath`` 引擎（作用于页内 JSON-LD）
- 返回的 ``SlotHit`` 带 ``confidence`` 与 ``evidence``

此前本模块用 ``lxml.cssselect`` 统一分派，仅特判 xpath，regex/jsonpath 落到
cssselect() 必抛 SelectorSyntaxError 并被吞成 None，再被记为 miss ——
而 ``scenes/annual_report.yaml`` 的 4 个基因全是 regex，即出厂场景 100%
命中该缺陷路径，持续往适应度统计写入假 miss。

不在支持类型内的基因跳过且**不写反馈**，否则每轮都会记一个必然失败的 miss，
把好基因持续压低。计数见 ``skipped_unsupported_type``。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

MAX_AUGMENT_FIELDS_PER_PAGE = 30

_LXML_SELECTOR_TYPES = frozenset({"css", "xpath"})
# 转交 doc_extractors 的类型（与 HTMLDocExtractor / TextDocExtractor
# 实际处理的 extractor_type 对齐；jsonpath 作用于页内 JSON-LD）
_DELEGATED_SELECTOR_TYPES = frozenset({"regex", "jsonpath", "text"})
_SUPPORTED_SELECTOR_TYPES = _LXML_SELECTOR_TYPES | _DELEGATED_SELECTOR_TYPES


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
        ``{"active", "augmented", "hit", "miss", "skipped_no_gene",
        "skipped_unsupported_type", "skipped_no_document"}`` 统计。
    """
    stats = {
        "active": False,
        "augmented": 0,
        "hit": 0,
        "miss": 0,
        "skipped_no_gene": 0,
        "skipped_unsupported_type": 0,
        "skipped_no_document": 0,
    }
    if not scene or not fields or not records:
        return stats
    db = Path(db_path)
    if not db.exists():
        return stats
    try:
        from lxml import html as lxml_html  # 可选依赖：仅 css/xpath 需要
    except Exception:  # noqa: BLE001
        lxml_html = None
    try:
        from ..doc_extractors.base import TextDocExtractor
        from ..doc_extractors.html import HTMLDocExtractor
        from ..extraction.extractors import decode_body
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

    # 2. 取页面文本 + 建 DOM（DOM 仅 css/xpath 需要，缺失时该两型跳过）
    try:
        from ..extraction.extractors import decode_body

        html_text = decode_body(result)
        document = lxml_html.fromstring(html_text) if lxml_html is not None else None
    except Exception:  # noqa: BLE001
        LOGGER.warning("基因增强：正文解析失败", exc_info=True)
        return stats

    # 3. 逐字段取最优基因，按类型分派（任何异常仅告警）
    try:
        from ..quality.gene_pool import GenePool
        from ..state.scene_store import SceneStore, SlotDefinition

        with SceneStore(db) as store:
            pool = GenePool(store)
            lxml_targets: dict[str, Any] = {}
            html_defs: list[Any] = []
            text_defs: list[Any] = []
            targets: dict[str, Any] = {}
            for field, record in missing.items():
                # min_trials=0：让 YAML 播种的冷启动基因也参与补提，
                # 否则 0 尝试基因被过滤，闭环永远无法启动（无反馈可积累）
                genes = pool.recommend(scene, field, limit=1, min_trials=0)
                if not genes:
                    stats["skipped_no_gene"] += 1
                    continue
                gene = genes[0]
                if gene.selector_type not in _SUPPORTED_SELECTOR_TYPES:
                    # 不支持的类型：跳过，且不写反馈——否则每轮都会记一个
                    # 必然失败的 miss，把好基因持续压低。
                    stats["skipped_unsupported_type"] += 1
                    continue
                if gene.selector_type in _LXML_SELECTOR_TYPES:
                    if document is None:
                        stats["skipped_no_document"] += 1
                        continue
                    lxml_targets[field] = (record, gene)
                    continue
                targets[field] = (record, gene)
                definition = SlotDefinition(
                    scene=scene,
                    slot_key=field,
                    extractor_type=gene.selector_type,
                    pattern=gene.selector,
                )
                (text_defs if gene.selector_type == "text" else html_defs).append(definition)

            # 转交类：一次解析、一次分派，避免逐字段重复建树
            hits: dict[str, Any] = {}
            for extractor, definitions in (
                (HTMLDocExtractor(), html_defs),
                (TextDocExtractor(), text_defs),
            ):
                for slot_hit in extractor.extract(html_text, definitions):
                    if slot_hit.value not in (None, "", []):
                        hits[slot_hit.slot_key] = slot_hit.value
            for field, (record, gene) in targets.items():
                _apply(pool, stats, scene, field, record, gene, hits.get(field))

            # css/xpath：沿用本模块原有语义（多节点 → list）
            for field, (record, gene) in lxml_targets.items():
                value = _extract_with_selector(document, gene.selector, gene.selector_type)
                _apply(pool, stats, scene, field, record, gene, value)
    except Exception:  # noqa: BLE001 — 基因增强失败绝不阻断主流程
        LOGGER.warning("基因增强失败: %s", exc_info=True)
    return stats


def _apply(
    pool: Any,
    stats: dict[str, Any],
    scene: str,
    field: str,
    record: Any,
    gene: Any,
    value: Any,
) -> None:
    """把一次补提结果写回记录并记反馈，命中/未命中共用。"""
    if value in (None, "", []):
        pool.record(scene, field, gene.selector, hit=False)
        stats["miss"] += 1
        return
    data = getattr(record, "data", None)
    if isinstance(data, dict):
        data[field] = value
    pool.record(scene, field, gene.selector, hit=True)
    stats["hit"] += 1
    stats["augmented"] += 1


def _extract_with_selector(document: Any, selector: str, selector_type: str) -> Any:
    """在已解析 HTML 上按基因选择器提取；单节点→str，多节点→list，空→None。

    仅服务 css/xpath 基因；regex/jsonpath/text 由 doc_extractors 负责。
    """
    if not selector or document is None:
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
