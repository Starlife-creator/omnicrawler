"""convertx：表格 / 文档互转工具层（独立 CLI / GUI 复用）的公共入口。

实现已下沉至 :mod:`omnicrawler.convertx._core`（长期债治理：``__init__`` 只做导出）；
完整设计说明与实现见 ``_core.py``。外部 ``from omnicrawler.convertx import ...``
用法完全不变。
"""

from ._core import *  # noqa: F403

# 向后兼容垫片：历史上这些名字可从包级导入（document.py / 单元测试曾如此使用）。
# 注：`from ._core import *` 不会导出下划线名，故这些必须显式列出。
from ._core import (  # noqa: F401
    __all__,
    _ensure_parent_dir,
    _ordered_columns,
    atomic_output,
    check_cancel,
)
