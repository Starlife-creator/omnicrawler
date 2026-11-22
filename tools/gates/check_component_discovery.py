"""反向索引：组件扫描必须走 `component_discovery`，不许各处自己写。

## 这个门禁解决什么

组件目录改成两级后，**六处**扫描点没跟着改，每一处报错都不一样，
我逐个撞出来（详见 `tools/market/component_discovery.py` 的说明）。

问题不在于那次改目录，而在于**「怎么找组件」这件事散落在各处**：
只要还有裸 `glob("*/recipe.json")`，下次改结构就还会漏。

## 判据

扫描 `src/` `tests/` `tools/`，找出同时满足下面两条的代码行：

1. 出现 `recipe.json`（说明在找组件配方）
2. 出现 `glob(` / `rglob(` / `iterdir(`（说明在自己做目录扫描）

这样的行必须**要么**调用 `iter_component_recipes`，**要么**在
`component_discovery.py` 内部（那里就是真源）。

## 为什么是「行级」而不是 AST

AST 更准，但要区分「扫 recipe.json」与「扫别的 json」需要数据流分析，
朴素实现会大量误报。行级 + 白名单在这个规模上足够，且误报可控
（误报时改法很明确：改用 `iter_component_recipes`）。

## 用法

```powershell
python tools/gates/check_component_discovery.py
```
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

#: 只扫这些目录。
_SCAN_DIRS = ("src", "tests", "tools")

#: 真源自己允许直接扫。
_EXEMPT_FILES = frozenset(
    {
        "tools/market/component_discovery.py",
        # 本文件的文档字符串里举了「裸 glob 是错的」这个例子，
        # 不是真的扫描 —— 豁免它，否则门禁会举报自己。
        "tools/gates/check_component_discovery.py",
    }
)

#: 目录扫描的调用形态。
_SCAN_PATTERN = re.compile(r"\b(glob|rglob|iterdir)\s*\(")

#: 配方文件名。
_RECIPE_TOKEN = "recipe.json"


def find_violations(root: Path) -> list[tuple[str, int, str]]:
    """返回 `(相对路径, 行号, 该行内容)` 列表。"""
    out: list[tuple[str, int, str]] = []
    for directory in _SCAN_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            if relative in _EXEMPT_FILES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # 用 AST 找出**文档字符串**占用的行区间，跳过它们 ——
            # 说明文字里举「裸 glob 是错的」这个例子不是真的扫描。
            doc_lines: set[int] = set()
            try:
                tree = ast.parse(text)
            except SyntaxError:
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                        body = getattr(node, "body", [])
                        if (
                            body
                            and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)
                        ):
                            start = body[0].lineno
                            end = body[0].end_lineno or start
                            doc_lines.update(range(start, end + 1))
            for number, line in enumerate(text.splitlines(), 1):
                if number in doc_lines:
                    continue
                stripped = line.strip()
                # **跳过注释与文档字符串里的示例**：本文件与
                # `component_discovery.py` 都会在说明里举「裸 glob 是错的」
                # 这种例子，那不是真的扫描。
                if stripped.startswith("#"):
                    continue
                if _RECIPE_TOKEN not in line:
                    continue
                if not _SCAN_PATTERN.search(line):
                    continue
                # 已经走真源的，放过
                if "iter_component_recipes" in line or "iter_component_dirs" in line:
                    continue
                out.append((relative, number, line.strip()))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查组件扫描是否都走 component_discovery",
    )
    parser.add_argument("--root", default="", help="仓库根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    violations = find_violations(root)

    # **防空集恒真**：扫描必须真的有收获，否则「没违规」可能只是正则失效。
    scanned = sum(
        1
        for directory in _SCAN_DIRS
        for path in (root / directory).rglob("*.py")
        if path.is_file()
    ) if any((root / d).is_dir() for d in _SCAN_DIRS) else 0
    if scanned < 100:
        print(
            f"{BAD_MARK} 只扫到 {scanned} 个 Python 文件 —— "
            "正则或扫描目录可能已失效（空集会让判据恒真）"
        )
        return 1

    if violations:
        print(f"{BAD_MARK} 组件扫描未走 component_discovery：")
        for relative, number, line in violations:
            print(f"  {relative}:{number}  {line[:100]}")
        print(
            "\n改用 `from tools.market.component_discovery import "
            "iter_component_recipes` ——\n"
            "组件按 `components/<分类>/<id>/recipe.json` 组织，"
            "一层 glob 扫不到任何东西，\n"
            "而报错会伪装成「未找到任何组件配方」，掩盖真实原因。"
        )
        return 1

    print(f"{OK_MARK} 组件扫描统一走 component_discovery（扫描 {scanned} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
