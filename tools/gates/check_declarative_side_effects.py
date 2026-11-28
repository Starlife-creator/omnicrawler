"""声明式视图的**副作用通道**必须与文档逐条一致（W6-D，2026-11-28）。

## 这个门禁解决什么

`docs/PLUGIN_CONTRACT.md` 里写着「一个声明式视图能触发的**全部**副作用只有四条」。
这句话对插件作者是**行为承诺** —— 他们据此判断「用声明式界面，我最多能做这些」。

一旦有人在渲染代码里加第五条通道（比如「顺手读个文件」「顺手发个请求」），
文档那句话就变成**假的**，而没有任何东西会红。这类漂移的代价特别高：
它是**安全承诺**的漂移，不是文案漂移。

## 判据

用 AST 找出 `declarative_view_host.py` 里 `self._adapter.<name>` 的**全部**调用，
与文档表格里的 `adapter.<name>(...)` 做**双向**比对：

- 代码有、文档没写 ⇒ 红（承诺不完整）；
- 文档写了、代码没有 ⇒ 红（承诺是空的）；
- 任一侧为 0 ⇒ 红（空集合会让断言恒真）。

## 为什么用 AST 而不是正则

正则会把注释与文档字符串里的示例也算进去，于是「文档写了但代码没有」永远测不出来。

## 用法

    python tools/gates/check_declarative_side_effects.py           # 人类可读
    python tools/gates/check_declarative_side_effects.py --check   # 有问题退出 1
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_HOST = _ROOT / "src" / "omnicrawler" / "gui" / "declarative_view_host.py"
_DOC = _ROOT / "docs" / "PLUGIN_CONTRACT.md"

#: 文档里那句承诺的**锚句**。改承诺措辞必须同步这里 —— 找不到就判红，
#: 而不是静默跳过（否则改个标题就能把门禁绕过去）。
_DOC_ANCHOR = "一个声明式视图能触发的**全部**副作用只有四条"


def adapter_calls(source: str) -> set[str]:
    """AST 找出 `self._adapter.<name>` 的全部属性名。"""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        if not isinstance(base, ast.Attribute):
            continue
        if not isinstance(base.value, ast.Name) or base.value.id != "self":
            continue
        if base.attr != "_adapter":
            continue
        names.add(node.attr.lstrip("_"))
    return names


def documented_channels(document: str) -> set[str]:
    """从文档表格里读 `| `adapter.<name>(...)` |` 形式的通道名。"""
    start = document.find(_DOC_ANCHOR)
    if start < 0:
        raise SystemExit(
            f"文档里找不到锚句「{_DOC_ANCHOR}」—— 承诺措辞变了就要同步门禁，"
            "不能靠改标题绕过"
        )
    end = document.find("\n## ", start)
    section = document[start:end if end > 0 else len(document)]
    return {
        match.split("(")[0].lstrip("_")
        for match in re.findall(r"\| `adapter\.([^`]+)`", section)
    }


def audit(root: Path = _ROOT) -> list[str]:
    """返回问题列表（空 = 通过）。"""
    host = root / _HOST.relative_to(_ROOT)
    doc = root / _DOC.relative_to(_ROOT)
    code = adapter_calls(host.read_text(encoding="utf-8"))
    written = documented_channels(doc.read_text(encoding="utf-8"))

    problems: list[str] = []
    undocumented = code - written
    if undocumented:
        problems.append(
            f"这些副作用通道在代码里但文档没写：{sorted(undocumented)}。"
            "插件作者会按残缺的清单评估风险。"
        )
    phantom = written - code
    if phantom:
        problems.append(f"文档承诺了不存在的通道：{sorted(phantom)}")
    if not code or not written:
        problems.append(
            f"扫描面为空（代码 {len(code)} 条 / 文档 {len(written)} 条）——"
            "空集合会让所有断言恒真"
        )
    if not problems and len(code) != len(written):
        problems.append(f"代码 {len(code)} 条 vs 文档 {len(written)} 条，数量不一致")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="有问题时退出 1")
    args = parser.parse_args(argv)
    problems = audit()
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 1 if args.check else 0
    code = adapter_calls(_HOST.read_text(encoding="utf-8"))
    print(f"[OK] 声明式视图副作用通道 {len(code)} 条，文档逐条覆盖：{sorted(code)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
