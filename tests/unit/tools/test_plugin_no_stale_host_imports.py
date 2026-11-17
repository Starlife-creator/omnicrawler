"""插件不得引用宿主已迁走的模块（静态导入检查）。

## 这条守卫防的是什么

2026-11-22 在插件里找到 5 处：

```python
from omnicrawler.convertx import OutputRule, resolve_output_path   # convertx 已不在宿主
from omnicrawler.convertx.catalog import format_status
from omnicrawler.pdfx.service import run_extraction                # pdfx 已不在宿主
from omnicrawler.pdfx.project import create_project_config
```

**它们都在函数内部**，所以：

* 导入期不报错（模块能正常 import）；
* 471 个插件测试**全绿**；
* 但用户点开"格式互转"或"PDF 工作台"时立刻 `ModuleNotFoundError`。

这正是"能 import ≠ 能调用"的又一例。测试覆盖不到的原因是：
GUI 视图只做了构造测试，没有触发那些交互回调。

## 判据

扫描插件全部 `.py`（含字符串里的导入示例），凡 `from omnicrawler.<migrated>`
或 `import omnicrawler.<migrated>` 即判红，其中 `<migrated>` 是**确认已迁出宿主**的包。

`omnicrawler.review` **不在此列**——它仍留在宿主，插件引用它是正确的。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PLUGIN = (
    Path(__file__).resolve().parents[4]
    / "OmniCrawler-market"
    / "submissions"
    / "plugins"
    / "4c3014804d85de2568f85e62a3048429"
    / "pdf-workbench"
)

#: 已从宿主迁出的包——插件里**不得**再出现 `from omnicrawler.<这些>...`。
_MIGRATED_OUT_OF_HOST = ("pdfx", "convertx", "document_ir", "apps")

#: 允许出现的（仍留在宿主，插件引用正确）。
_ALLOWED_HOST_PACKAGES = ("review",)

_PATTERN = re.compile(
    r"(?:from|import)\s+omnicrawler\.(" + "|".join(_MIGRATED_OUT_OF_HOST) + r")\b"
)


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_plugin_has_no_references_to_migrated_out_modules() -> None:
    """插件不得 `from omnicrawler.pdfx|convertx|document_ir|apps`。

    这些包已迁出宿主，引用必然运行时失败。
    """
    offenders: list[str] = []
    for path in sorted(_PLUGIN.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PATTERN.search(line):
                rel = path.relative_to(_PLUGIN)
                offenders.append(f"{rel}:{number}  {line.strip()[:110]}")

    assert not offenders, (
        "插件引用了已迁出宿主的模块（运行时会 ModuleNotFoundError）：\n  "
        + "\n  ".join(offenders)
        + "\n\n应改为插件内的相对导入（如 `from ..pdfx.service import ...`）。"
    )


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_review_imports_are_allowed() -> None:
    """`omnicrawler.review` 仍留在宿主，插件引用它是**正确**的。

    这条不是"也要检查"，而是把边界写清楚：避免有人看到
    `from omnicrawler.…` 就一律改成相对导入，把真正该留的引用也改坏。
    """
    review_hits: list[str] = []
    for path in sorted(_PLUGIN.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(f"omnicrawler.{pkg}" in line for pkg in _ALLOWED_HOST_PACKAGES):
                review_hits.append(f"{path.relative_to(_PLUGIN)}:{number}")

    # 至少有一处在用（plugin.py 的 review.compare）——因为 review 确实留宿主
    assert review_hits, (
        "没有找到任何 omnicrawler.review 引用。"
        "如果 review 也被迁进插件了，请更新本测试的边界说明"
    )
