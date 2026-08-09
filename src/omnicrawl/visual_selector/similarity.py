"""相似元素检测引擎 — Python 移植 EasySpider findRelated / relatedTest 算法。

核心思路：给定用户选中元素的 XPath，逐层去掉索引号，
用更通用的 XPath 查询页面，找到匹配 2+ 个元素的"同类组"。
"""

from __future__ import annotations

import re
from typing import Any


def _split_xpath(xpath: str) -> tuple[list[str], list[int]]:
    """将 XPath 拆分为标签名列表和索引列表。

    '/html/body/div[3]/div[1]/a[2]' → (['html','body','div','div','a'], [-1,-1,3,1,2])
    """
    # 去掉 //iframe 前缀
    xpath = re.sub(r"^//iframe", "", xpath)
    segments = [s for s in xpath.split("/") if s]
    names: list[str] = []
    indices: list[int] = []
    for seg in segments:
        match = re.match(r"^(\w+)(?:\[(\d+)\])?$", seg)
        if match:
            names.append(match.group(1))
            indices.append(int(match.group(2)) if match.group(2) else -1)
        else:
            names.append(seg)
            indices.append(-1)
    return names, indices


def _combine_xpath(names: list[str], indices: list[int]) -> str:
    """用标签名列表和索引列表重建 XPath。

    (['html','body','div'], [-1,-1,3]) → '/html/body/div[3]'
    """
    parts: list[str] = []
    for name, idx in zip(names, indices, strict=False):
        if idx >= 0:
            parts.append(f"{name}[{idx}]")
        else:
            parts.append(name)
    return "/" + "/".join(parts)


def find_similar_elements(
    lxml_doc: Any,
    xpath: str,
) -> tuple[str | None, list[Any]]:
    """给定用户选中元素的 XPath，查找页面中所有同类元素。

    Args:
        lxml_doc: lxml HTML 文档对象。
        xpath: 用户选中元素的绝对 XPath。

    Returns:
        (通用 XPath, 匹配的元素列表)。如果找不到同类则返回 (None, [])。
    """
    names, indices = _split_xpath(xpath)
    if not names:
        return None, []

    # 从深到浅，逐层去掉索引号
    for i in range(len(indices) - 1, -1, -1):
        if indices[i] == -1:
            continue
        test_indices = list(indices)
        test_indices[i] = -1
        test_xpath = _combine_xpath(names, test_indices)
        try:
            elements = lxml_doc.xpath(test_xpath)
        except Exception:
            continue
        if len(elements) >= 2:
            return test_xpath, elements
    return None, []


def generate_common_xpath(xpaths: list[str]) -> str | None:
    """为多个已选中元素的 XPath 生成通用 XPath（去掉索引差异位置）。

    例：
        /html/body/div[3]/div[1]/a[22]
        /html/body/div[3]/div[2]/a[25]
        → /html/body/div[3]/div/a
    """
    if not xpaths:
        return None
    if len(xpaths) == 1:
        return xpaths[0]

    parsed = [_split_xpath(xp) for xp in xpaths]
    if not all(len(p[0]) == len(parsed[0][0]) for p in parsed):
        return None  # 结构不同，无法合并

    common_names = parsed[0][0]
    common_indices: list[int] = []
    for col in range(len(common_names)):
        vals = {p[1][col] for p in parsed}
        if len(vals) == 1 and -1 not in vals:
            common_indices.append(next(iter(vals)))
        else:
            common_indices.append(-1)

    return _combine_xpath(common_names, common_indices)


class SimilarityEngine:
    """离线相似度引擎 — 不依赖浏览器，传入 lxml 文档即可使用。"""

    def __init__(self) -> None:
        self._last_common_xpath: str | None = None
        self._last_elements: list[Any] = []

    def scan(self, lxml_doc: Any, xpath: str) -> list[dict[str, Any]]:
        """扫描文档中与给定 XPath 相似的所有元素。

        Returns:
            元素信息列表，每项含 {tag, text, xpath, attributes}。
        """
        common_xpath, elements = find_similar_elements(lxml_doc, xpath)
        self._last_common_xpath = common_xpath
        self._last_elements = elements
        result: list[dict[str, Any]] = []
        for el in elements:
            info: dict[str, Any] = {
                "tag": str(el.tag) if hasattr(el, "tag") else "",
                "text": (el.text_content() if hasattr(el, "text_content") else str(el)).strip()[:200],
            }
            if hasattr(el, "attrib"):
                info["attributes"] = dict(el.attrib)
            result.append(info)
        return result

    @property
    def common_xpath(self) -> str | None:
        return self._last_common_xpath

    @property
    def element_count(self) -> int:
        return len(self._last_elements)
