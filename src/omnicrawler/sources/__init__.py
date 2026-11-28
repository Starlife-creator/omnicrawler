"""OmniCrawler sources subpackage.

## 为什么这里不再 `from .sources import GenericSource`（2026-11-28）

`sources/sources.py` 是重模块：`defusedxml` / `extraction.extractors` /
`extraction.html_tools`（连带 **bs4**，约 74 ms）/ `fetching.http_client`。

在包的 `__init__` 里导入它，意味着**任何** `omnicrawler.sources.*` 的导入都会连带
整条链 —— 包括只需要 `sources.kinds` 里那 22 个字符串的 `gui/core/validator.py`。

实测：`gui/core/validator.py` 的导入因此从 108.7 ms 涨到 253 ms（+144 ms），
而它一行 DOM 解析都不做。

## 兼容性

`from omnicrawler.sources import GenericSource` 仍然可用 —— 通过模块级
`__getattr__`（PEP 562）**惰性**取。这样：

- 老调用方零改动；
- 只要不碰 `GenericSource`，就不会拉起 bs4；
- `__all__` 保留，`import *` 与静态检查工具行为不变。

★ 这是标准库 PEP 562 的官方用法，不是 hack。
"""

from __future__ import annotations

from typing import Any

__all__ = ["GenericSource"]


def __getattr__(name: str) -> Any:
    if name == "GenericSource":
        from .sources import GenericSource as _GenericSource

        return _GenericSource
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
