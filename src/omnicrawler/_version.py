"""版本号叶子模块 —— 让 ``omnicrawler`` 包不再因 ``__version__`` 成为导入环枢纽。

``omnicrawler/__init__.py`` 与包内十余处模块都需要版本号。此前的写法是各处
``from .. import __version__``（指向**包**），于是包被拉进一个 60+ 模块的大型
静态导入环 —— ``tools/check_architecture.py`` 的 cycle budget 因此长期超标。

把版本号下沉到本叶子模块（不导入任何包内模块）后，各引用点改为
``from .._version import __version__``，包不再是枢纽；对外契约不变：
``omnicrawler.__version__`` 仍可用（包从本模块 re-export）。
"""

from __future__ import annotations

# Keep this value import-safe for source checkouts. Packaging metadata is
# verified against it by tools/check_docs_consistency.py before release.
__version__ = "0.12.0"
