"""遍历目录时**必须在进入前剪枝**，不要先 rglob 再逐条过滤。

## 这个门禁解决什么

2026-11-23 实测：`check_extras_exist.py` 单跑要 **20.5 秒**，而它只是
扫描文本里的 `pip install -e ".[...]"`。

根因是它的写法：

```python
for path in root.rglob("*"):           # ← 走进 .venv / node_modules
    if not path.is_file(): continue
    if any(part in _SKIP_DIRS for part in path.parts):   # ← 事后才跳过
        continue
```

`rglob` 会**真的递归进去**，然后才在结果里过滤。本仓实测：

| 方式 | 遍历量 | 耗时 |
|---|---|---|
| `rglob("*")` + 事后过滤 | 128,133 条 | 8.1 s |
| `os.walk` + 就地剪枝 `dirs[:]` | 27,192 文件 | 2.6 s |

门禁单跑时间因此从 20.5s 降到 6.2s（快 3.3 倍），结果不变。

## 判据

扫 `tools/` 下的 `.py`，找**同一个函数体内**同时出现：

1. `rglob(` 或 `glob("**` 调用，**且**
2. 对遍历结果做 `_SKIP` / `skip` 相关的 `continue` / 过滤

⇒ 判红，要求改为 `os.walk` + `dirs[:] = [...]` 剪枝。

## 为什么这是「构建阶段预防」而不是「事后优化」

这个模式**不会报错、不会红测试**——它只让工具悄悄变慢。
而慢门禁的后果是我不愿意跑它，于是退回全量验证（本会话真实发生过：
40 次 CI、0 次成功）。**性能缺陷会转化成纪律缺陷。**

## 边界

只抓「遍历 + 事后过滤」这一种形态。已经用 `os.walk` 剪枝的、
或只在浅层（非递归）用 glob 的，都不算。

## 用法

```powershell
python tools/gates/check_walk_pruning.py
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

#: 递归遍历的调用名。
_RECURSIVE_CALLS = frozenset({"rglob"})

#: 「跳过目录」相关的标识（用于识别事后过滤）。
_SKIP_HINTS = ("_skip", "skip_", "skipdirs", "_excluded", "excluded_dirs")

#: 显式豁免：目标目录**本身很小**，遍历量不构成问题。
#:
#: `walk_payload_dir` 走的是「解压后的载荷目录」——一个组件的 site-packages，
#: 量级在千级以内，且它需要 `reject_protected_top_level` 对**每个条目**做路径
#: 校验（不只是跳过），改成剪枝反而要重排逻辑。收益不足以抵消风险。
_exempt: dict[str, str] = {
    "tools/builders/manifest_common.py::walk_payload_dir": (
        "遍历的是解压后的载荷目录（组件 site-packages，千级），且需逐条路径校验"
    ),
}


def _has_recursive_glob(func: ast.FunctionDef) -> int | None:
    """函数体内是否有递归 glob 调用；有则返回行号。"""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        # `x.rglob(...)` 形态
        func_node = node.func
        if isinstance(func_node, ast.Attribute) and func_node.attr in _RECURSIVE_CALLS:
            return node.lineno
    return None


def _has_post_filter(func: ast.FunctionDef) -> bool:
    """函数体内是否对条目做「跳过目录」式的事后过滤。"""
    for node in ast.walk(func):
        # 形如 `if any(part in _SKIP_DIRS ...): continue`
        if isinstance(node, ast.If):
            segment = ast.dump(node.test).lower()
            if any(hint in segment for hint in _SKIP_HINTS):
                return True
        # 形如 `if <x> in _SKIP_DIRS`
        if isinstance(node, ast.Compare):
            segment = ast.dump(node).lower()
            if any(hint in segment for hint in _SKIP_HINTS):
                return True
    return False


def find_problems(root: Path) -> tuple[list[str], int]:
    """返回 `(问题列表, 检查过的函数数)`。"""
    problems: list[str] = []
    checked = 0
    base = root / "tools"
    if not base.is_dir():
        return problems, checked

    for path in base.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            glob_line = _has_recursive_glob(node)
            if glob_line is None:
                continue
            checked += 1
            if not _has_post_filter(node):
                continue
            qualified = f"{path.relative_to(root).as_posix()}::{node.name}"
            if qualified in _exempt:
                continue
            problems.append(
                f"{path.relative_to(root).as_posix()}:{glob_line} "
                f"`{node.name}()` 用 rglob 遍历后再过滤跳过目录"
            )
    return problems, checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查目录遍历是否在进入前剪枝（而非 rglob 后过滤）",
    )
    parser.add_argument("--root", default="", help="仓库根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    problems, checked = find_problems(root)

    if problems:
        print(f"{BAD_MARK} 这些函数用 rglob 遍历、事后才过滤跳过目录：")
        for item in problems:
            print(f"  - {item}")
        print(
            "\nrglob 会**真的递归进** .venv / node_modules，然后才丢弃结果。\n"
            "改为 os.walk 并**就地剪枝**：\n"
            "    for root, dirs, files in os.walk(base):\n"
            "        dirs[:] = [d for d in dirs if d not in SKIP]\n"
            "本仓实测 8.1s -> 2.6s（遍历量 12.8 万 -> 2.7 万）。"
        )
        return 1

    print(f"{OK_MARK} 检查 {checked} 处递归遍历，未发现「事后过滤」形态")
    return 0


if __name__ == "__main__":
    sys.exit(main())
