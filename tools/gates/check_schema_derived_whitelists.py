"""配置段的白名单必须从 schema 派生，不得手抄。

## 这个门禁解决什么

2026-11-22：我给 `browser` 段加了 `kernel_family`（内核持久化），
但 `templates/workflow_capture.py` 里有一份**手写白名单**：

```python
allowed_browser = {"engine", "headless", "pool_size", ..., "selenium_fallback_engine"}
if set(browser) - allowed_browser:
    raise ValueError("浏览器配置含未支持属性，不能静默移除")
```

新增字段没同步 ⇒ 用 browser 源采模板时被这条判据拒掉，报错只说
「含未支持属性」，**不说是哪个**。排查花了一轮 CI。

真正的修复不是「记得同步」，而是**取消手抄**：
`schema`（`config_defaults.DEFAULTS`）是唯一真源，白名单从它派生。

## 判据

扫 `src/` 下所有 `.py`，找形如

```python
allowed_<name> = {...}          # 字面量集合
```

且该集合的**元素恰好是某个 schema 段的键名**（覆盖率 ≥ 60%），
说明它在手抄 schema ⇒ 判红，要求改为从 `DEFAULTS[<段>]` 派生。

**为什么用「覆盖率 ≥ 60%」而不是「完全相等」**：
白名单常只覆盖 schema 的子集（有些键不适合模板化），
完全相等会误报；而手抄的特征是「大量重合」。

## 边界

它只抓「手抄 schema 键名集合」这一种形态。其它重复定义
（例如两处常量各自维护）不在范围内 —— 那些需要逐案判断。

## 用法

```powershell
python tools/gates/check_schema_derived_whitelists.py
```
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 视为「可能在手抄 schema」的变量名前缀。
_SUSPECT_PREFIXES = ("allowed_", "_allowed_", "ALLOWED_", "_ALLOWED_")

#: 视为「手抄」的重合比例阈值。
_OVERLAP_THRESHOLD = 0.6

#: 最少元素数（太小的集合重合没有统计意义）。
_MIN_SIZE = 4


def _schema_keys() -> dict[str, set[str]]:
    """从 `config_defaults.DEFAULTS` 取每个段的键名。"""
    from omnicrawler.core.config_defaults import DEFAULTS

    return {
        section: set(value)
        for section, value in DEFAULTS.items()
        if isinstance(value, dict) and len(value) >= _MIN_SIZE
    }


def _literal_str_set(node: ast.AST) -> set[str] | None:
    """如果节点是「字符串字面量集合」，返回元素；否则 None。"""
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        items: set[str] = set()
        for element in node.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                items.add(element.value)
            else:
                return None
        return items
    return None


def find_problems(root: Path) -> tuple[list[str], int]:
    """返回 `(问题列表, 检查到的候选集合数)`。"""
    schemas = _schema_keys()
    problems: list[str] = []
    checked = 0

    base = root / "src"
    if not base.is_dir():
        return problems, checked

    for path in base.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if not name.startswith(_SUSPECT_PREFIXES):
                continue
            items = _literal_str_set(node.value)
            if items is None or len(items) < _MIN_SIZE:
                continue
            checked += 1

            # 找重合度最高的 schema 段。
            best_section = ""
            best_ratio = 0.0
            for section, keys in schemas.items():
                overlap = len(items & keys)
                ratio = overlap / len(items)
                if ratio > best_ratio:
                    best_ratio, best_section = ratio, section

            if best_ratio < _OVERLAP_THRESHOLD:
                continue

            line = node.lineno
            problems.append(
                f"{path.relative_to(root).as_posix()}:{line} `{name}` "
                f"与 schema 段 `{best_section}` 重合 {best_ratio:.0%}"
                f"（{len(items & schemas[best_section])}/{len(items)}）——像是手抄"
            )

    return problems, checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查配置白名单是否手抄了 schema 键名",
    )
    parser.add_argument("--root", default="", help="仓库根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    problems, checked = find_problems(root)

    if problems:
        print(f"{BAD_MARK} 这些白名单疑似手抄了 schema 键名：")
        for item in problems:
            print(f"  - {item}")
        print(
            "\n手抄的后果：schema 加字段时这里不会同步，而报错通常只说"
            "「含未支持属性」、不说是哪个，排查成本高。"
            "改为从 `config_defaults.DEFAULTS[<段>]` 派生。"
        )
        return 1

    print(f"{OK_MARK} 检查 {checked} 处白名单，未发现手抄 schema")
    return 0


if __name__ == "__main__":
    sys.exit(main())
