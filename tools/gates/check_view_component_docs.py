"""`view` 组件清单：代码是真源，文档必须逐个覆盖。

## 这个门禁解决什么

2026-11-27：`docs/PLUGIN_CONTRACT.md` 写市场 `view` 只允许 **7 种**组件，
而 `plugins/plugin_declarative.py` 的 `COMPONENT_TYPES` 是 **9 种** ——
`text`（单行输入）与 `progress`（运行进度）是 2026-09-24 新增的，
宿主渲染已完整实现（`gui/declarative_view_host.py`），文档却一直没跟上。

这类漂移**不会报错、不会红测试**：插件作者按文档写 `view`，会发现 `text` 用不了；
或反过来，看到代码里有 `text` 但文档没说，不敢用。两种都是文档在骗人。

## 判据

导入 `plugin_declarative.COMPONENT_TYPES`，断言**每一个**组件名都出现在
`docs/PLUGIN_CONTRACT.md` 的正文里。

- **空集合判红**：`COMPONENT_TYPES` 为空会让下面所有断言恒真（本项目反复踩过）。
- 只认「名字出现在文档正文」这一件事，不试图校验描述文字是否准确 ——
  那是人工审阅的范围（`docs/PLUGIN_CONTRACT.md` 有逐组件语义段）。

## 边界

不检查 `SDK_USAGE.md` 之类概述性文档：它们本来就不该逐条列组件，
把清单钉在一处、其余文档指过去，才是可维护的形态。

## 用法

```powershell
python tools/gates/check_view_component_docs.py
```
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

#: 文档清单的唯一检查对象（其余文档应指过来，不重复列）。
_DOC = Path("docs") / "PLUGIN_CONTRACT.md"


def component_types() -> frozenset[str]:
    """代码里的 `view` 组件清单（唯一真源）。"""
    from omnicrawler.plugins.plugin_declarative import COMPONENT_TYPES

    return frozenset(COMPONENT_TYPES)


def missing_components(document: str, components: frozenset[str]) -> list[str]:
    """文档里**没有出现**的组件名（按名字出现判定，不解析 Markdown 结构）。"""
    return sorted(
        name for name in components
        if not re.search(r"\b" + re.escape(name) + r"\b", document)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 view 组件清单在文档里是否齐全")
    parser.add_argument("--root", default="", help="仓库根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    doc_path = root / _DOC
    if not doc_path.is_file():
        print(f"{BAD_MARK} 找不到文档：{_DOC}")
        return 1

    components = component_types()
    if not components:
        print(
            f"{BAD_MARK} `COMPONENT_TYPES` 为空 —— 空集合会让本检查恒真，必须判红。\n"
            "  真源：src/omnicrawler/plugins/plugin_declarative.py"
        )
        return 1

    document = doc_path.read_text(encoding="utf-8")
    missing = missing_components(document, components)
    if missing:
        print(f"{BAD_MARK} 这些 view 组件在代码里有、文档里没提：")
        for name in missing:
            print(f"  - {name}")
        print(
            f"\n代码真源：src/omnicrawler/plugins/plugin_declarative.py（共 {len(components)} 种）\n"
            f"文档应逐个列出：{_DOC}\n"
            "修法：把缺的组件补进清单，并写清它的值如何上送/刷新。"
        )
        return 1

    print(f"{OK_MARK} view 组件 {len(components)} 种，文档 {_DOC} 全部覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
