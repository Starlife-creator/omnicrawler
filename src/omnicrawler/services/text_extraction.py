"""通用文本抽取（支柱①的数据加工能力）。

**为什么在这里**

`quality/gene_augment.py`（基因增强）需要把 regex / jsonpath / text 三种规则
应用到采集到的 HTML/文本上。它原先直接：

    from ..doc_extractors.base import TextDocExtractor
    from ..doc_extractors.html import HTMLDocExtractor

而 `doc_extractors` 被归在"文档子系统"里。于是支柱①的数据加工**反向依赖**
文档工作台——文档工作台组件化后基因增强会失效。

但 `TextDocExtractor` / `HTMLDocExtractor` 的**能力本身**是通用的：
它们是"按规则从文本里取值"，与 PDF 无关。所以正确做法不是重写，
而是**把入口放到宿主侧**，内部复用既有实现（不产生两份逻辑）。

本模块提供一个稳定入口 `extract_text_field`，以及一个"获取抽取器对"的
内部分发器供 `gene_augment` 使用。**实现仍是 `doc_extractors` 的那两个类**
——搬的是归属与入口，不是逻辑。
"""
from __future__ import annotations

from typing import Any

from ..doc_extractors.base import TextDocExtractor
from ..doc_extractors.html import HTMLDocExtractor

__all__ = ["HTMLDocExtractor", "TextDocExtractor", "extract_text_field", "extractor_pairs"]


def extractor_pairs() -> tuple[tuple[Any, Any], ...]:
    """返回可用的抽取器实例对（HTML / 纯文本）。

    供批量分派使用：一次解析、一次分派，避免逐字段重复建树
    （这与 `gene_augment` 原有的性能取向一致）。
    """
    return (
        (HTMLDocExtractor(), "html"),
        (TextDocExtractor(), "text"),
    )


def extract_text_field(source: str, *, source_type: str = "text", selector: str = "") -> Any:
    """从一段文本/HTML 里按规则取一个字段值。

    这是**宿主侧的稳定入口**：调用方不必知道底层是哪个抽取器类，
    将来替换实现（例如支持 XPath 直取）时只需改这里。

    :param source: HTML 或纯文本内容。
    :param source_type: ``"html"`` / ``"css"`` 走 HTML 抽取器；
        ``"text"`` / ``"regex"`` / ``"jsonpath"`` 走纯文本抽取器
        （与 ``SlotDefinition.extractor_type`` 的取值一致）。
    :param selector: 规则表达式；空串表示原样返回 ``source``
        （便于测试与简单场景）。
    """
    if not selector:
        return source

    from ..state.scene_store import SlotDefinition

    uses_html = source_type in {"html", "css", "xpath"}
    extractor: Any = HTMLDocExtractor() if uses_html else TextDocExtractor()
    definition = SlotDefinition(
        scene="adhoc",
        slot_key="value",
        extractor_type=source_type,
        pattern=selector,
    )
    for hit in extractor.extract(source, [definition]):
        if getattr(hit, "slot_key", "") == "value":
            return hit.value
    return None
