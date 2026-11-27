"""宿主生命周期前缀表的每一项都必须对应**真实存在**的能力操作。

## 这个门禁解决什么

`plugin_subprocess_adapter._HOST_LIFECYCLE_OPERATION_PREFIXES` 决定「哪些操作不带
`run_id`」。它是一个**语义声明**：列在这里的前缀，宿主承诺「这类操作可能发生在任何一次
run 之外」。

2026-11-27 实测：表里有三项 `view.` / `resource.` / `capability.`，而全仓
**没有任何 `capability.*` 操作** —— `CAPABILITY_VERSIONS` 里没有、
`_CAPABILITY_PERMISSIONS` 里没有、各 Broker Mixin 也没有 `_cap_capability_*` 处理器。

一段永远不会命中的空预留，危害不是"多写一行"：它让读代码的人以为
「运行期能力协商是存在的」，从而在别处（例如插件作者文档里）把它当既有机制描述。

## 判据

对前缀表里的**每一项**，断言在「已知操作全集」里至少有一个同前缀操作。

「已知操作全集」= `CAPABILITY_VERSIONS`（宿主声明的能力）+ 适配器自己发起的操作
（`self._host.call("<op>", ...)` 的字面量）。两处合起来才是真实操作面 ——
只查前者会漏掉 `view.describe` / `resource.inventory` 这类由适配器发起、
但不进能力版本表的操作。

**空集合判红**：前缀表为空、或已知操作全集为空，都会让断言恒真。

## 边界

只在**前缀**层面断言存在性，不校验每个操作是否真有处理器 ——
那是 `_broker.dispatch` 的运行期职责（未知操作会 `E_CONTRACT`）。

## 用法

```powershell
python tools/gates/check_lifecycle_prefixes.py
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

_ADAPTER = Path("src") / "omnicrawler" / "plugins" / "plugin_subprocess_adapter.py"

#: 适配器发起操作的调用形态：`self._host.call("op.name", ...)`。
_CALL_RE = re.compile(r'_host\.call\("([a-z][a-z0-9_.]*)"')


def known_operations(root: Path) -> frozenset[str]:
    """已知操作全集：能力版本表 ∪ 适配器发起的操作。"""
    from omnicrawler.plugins.plugin_broker_contracts import CAPABILITY_VERSIONS

    adapter = (root / _ADAPTER).read_text(encoding="utf-8")
    return frozenset(CAPABILITY_VERSIONS) | frozenset(_CALL_RE.findall(adapter))


def lifecycle_prefixes(root: Path) -> tuple[str, ...]:
    """从前缀表里读出现有前缀（不 import，避免把模块级状态带进来）。"""
    from omnicrawler.plugins.plugin_subprocess_adapter import (
        _HOST_LIFECYCLE_OPERATION_PREFIXES,
    )

    return tuple(_HOST_LIFECYCLE_OPERATION_PREFIXES)


def uncovered_prefixes(prefixes: tuple[str, ...], operations: frozenset[str]) -> list[str]:
    """没有对应真实操作的前缀。"""
    return [prefix for prefix in prefixes if not any(op.startswith(prefix) for op in operations)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查宿主生命周期前缀是否都有真实操作")
    parser.add_argument("--root", default="", help="仓库根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    prefixes = lifecycle_prefixes(root)
    operations = known_operations(root)

    if not prefixes:
        print(
            f"{BAD_MARK} 生命周期前缀表为空 —— 空集合会让本检查恒真，必须判红。"
            "\n  真源：src/omnicrawler/plugins/plugin_subprocess_adapter.py"
        )
        return 1
    if not operations:
        print(
            f"{BAD_MARK} 已知操作全集为空 —— 无法证明任何前缀有对应操作，必须判红。"
        )
        return 1

    uncovered = uncovered_prefixes(prefixes, operations)
    if uncovered:
        print(f"{BAD_MARK} 这些前缀在全仓没有任何对应操作（空预留）：")
        for prefix in uncovered:
            print(f"  - {prefix!r}")
        print(
            "\n前缀表声明的是「这类操作可能发生在 run 之外」。\n"
            "列一个永远不会命中的前缀，会让读代码的人以为那条机制已经存在。\n"
            "修法：要么补上真实操作，要么从表里删掉。"
        )
        return 1

    print(
        f"{OK_MARK} 生命周期前缀 {len(prefixes)} 项，均能在 {len(operations)} 个已知操作里找到对应"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
