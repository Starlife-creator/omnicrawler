"""`load_config()` 已自动创建 workspace 目录，测试不得再无条件 `mkdir`。

## 这个门禁解决什么

`load_config()` 解析 `project.workspace` 时**会创建该目录**（并存进
`config.workspace`）。于是测试里这种写法必然失败：

```python
config = load_config(tmp_path / "task.yaml")
config.workspace.mkdir(parents=True)      # ← FileExistsError
```

2026-11-22 实测报错：

```
FileExistsError: [WinError 183] 当文件已存在时，无法创建该文件。
  'E:\\tool\\biancheng\\omnicrawler\\OmniCrawler\\work'
```

当时 `test_workflow_diagnostics.py` 有 2 条、`test_recovery_center.py` 有 1 条
踩了这个坑。修法是加 `exist_ok=True` —— 语义上本来就是「确保目录存在」，
而不是「断言它不存在」。

## 为什么值得单独设一条门禁

它**只在特定执行顺序下暴露**：如果前一条测试刚好清掉了 `work/`，
这一条就能过；换个顺序就红。这类「顺序敏感」的缺陷极难在本地复现，
但在 CI（或别人机器上）会稳定出现。用静态检查一次扫掉最划算。

## 判据

扫 `tests/` 下所有 `.py`，找形如

```python
<something>.workspace.mkdir(parents=True)
```

且**没有** `exist_ok=True` 的调用 ⇒ 判红。

只查 `.workspace.mkdir`：其它目录的 `mkdir(parents=True)` 是普通用法，
不由 `load_config` 预先创建，不属于本条。

## 边界

* 它不判断「该不该建目录」，只保证「建的时候允许它已存在」。
* 若某测试**故意**断言目录不存在，应写 `assert not ...exists()`，
  而不是靠不带 `exist_ok` 的 `mkdir` 抛异常。
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

#: 匹配 `xxx.workspace.mkdir(...)`，随后单独检查是否含 exist_ok。
_WORKSPACE_MKDIR = re.compile(r"\.workspace\.mkdir\((?P<args>[^)]*)\)")

#: 检查范围（相对仓库根）。
_SCAN_ROOTS = ("tests",)


def find_problems(root: Path) -> tuple[list[str], int]:
    """返回 `(问题列表, 检查到的调用数)`。"""
    problems: list[str] = []
    checked = 0
    for scan_root in _SCAN_ROOTS:
        base = root / scan_root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # **用 AST 找出真正的调用位置**，而不是正则扫全文。
            #
            # 早先用正则直接从文本里找，于是把这些都当成"调用"：
            #   * 注释行 `# 例如 config.workspace.mkdir(...)`
            #   * docstring 里讲解用法的那句话
            # 反向断言时它报了 5 处，其中 2 处根本不是代码。
            #
            # AST 只给真实的 `Call` 节点，注释与字符串天然被排除。
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            real_lines: set[int] = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # 形如 `<x>.workspace.mkdir`
                if not isinstance(func, ast.Attribute) or func.attr != "mkdir":
                    continue
                target = func.value
                if not isinstance(target, ast.Attribute) or target.attr != "workspace":
                    continue
                real_lines.add(node.lineno)

            for match in _WORKSPACE_MKDIR.finditer(text):
                line = text[: match.start()].count("\n") + 1
                if line not in real_lines:
                    continue
                checked += 1
                args = match.group("args")
                if "exist_ok" in args:
                    continue
                problems.append(
                    f"{path.relative_to(root).as_posix()}:{line} "
                    f"`.workspace.mkdir({args.strip()})` 缺 exist_ok=True"
                )
    return problems, checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查测试里 .workspace.mkdir 是否都带 exist_ok=True",
    )
    parser.add_argument("--root", default="", help="仓库根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    problems, checked = find_problems(root)

    # **防空集恒真**：一处都没扫到，说明正则或扫描范围失效了。
    if checked == 0:
        print(
            f"{BAD_MARK} 没扫到任何 `.workspace.mkdir` 调用 —— "
            "正则或范围可能已失效（空集会让判据恒真）"
        )
        return 1

    if problems:
        print(f"{BAD_MARK} 这些测试会在目录已存在时报 FileExistsError：")
        for item in problems:
            print(f"  - {item}")
        print(
            "\n`load_config()` 解析 project.workspace 时**已经创建了**该目录。"
            "把 `mkdir(parents=True)` 改成 `mkdir(parents=True, exist_ok=True)` —— "
            "本意是「确保存在」，不是「断言不存在」。"
        )
        return 1

    print(f"{OK_MARK} {checked} 处 `.workspace.mkdir` 都带了 exist_ok=True")
    return 0


if __name__ == "__main__":
    sys.exit(main())
