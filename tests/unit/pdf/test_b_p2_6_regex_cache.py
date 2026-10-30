"""B-P2-6：别名动态正则按别名缓存；标题分块正则为模块级常量。"""

from __future__ import annotations

import re

from omnicrawler.extraction import ai_graph
from omnicrawler.pdfx.extraction import _alias_pattern


def test_alias_pattern_cached_per_alias() -> None:
    _alias_pattern.cache_clear()
    first = _alias_pattern("单价")
    second = _alias_pattern("单价")
    third = _alias_pattern("金额")
    assert first is second  # 同一别名复用已编译对象
    assert third is not first
    info = _alias_pattern.cache_info()
    assert info.hits >= 1 and info.currsize == 2
    match = first.search("单价: 12.5 元")
    assert match is not None and match.group("value") == "12.5 元"


def test_heading_split_regex_is_module_level_compiled() -> None:
    pattern = ai_graph._HEADING_SPLIT_RE
    assert isinstance(pattern, re.Pattern)
    match = pattern.search("<h2>价格</h2>正文")
    assert match is not None and match.group(1) == "h2" and match.group(2) == "价格"
