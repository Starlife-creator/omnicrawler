#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""能力表 ⇄ 代码 必须一致：`CAPABILITY_VERSIONS` 的每一条都得在文档里有交代。

## 为什么需要这道门

`docs/PLUGIN_CONTRACT.md` 的能力表是**插件作者唯一能看到的清单**。
它一旦落后于代码，作者会按错的清单写插件 —— 而这类漂移**不报错、不红测试**：

```
代码有、文档无（修前 17 条）: artifact.stream.*, resources.*, view.progress,
                            surface.background.*, component.ocr.recognize, …
文档有、代码无（修前 3 条）: source.file / source.files / source.input_file
```

`source.*` 那三条尤其糟：**文档让作者去声明一个不存在的能力**，
而 `validate_required_capabilities` 会当场拒载 —— 作者按文档写反而加载失败。

## 判据

1. `CAPABILITY_VERSIONS` 的每个键，必须在文档里出现（表格行或用反引号点名）；
2. 允许**声明式能力**（只声明、不可调用）—— 但必须显式列进
   `_DECLARATION_ONLY`，不能靠"文档里没写"来蒙混；
3. `_DECLARATION_ONLY` 里的每一条必须**真的**在代码里没有处理器 ——
   否则它是「本该可调用却被漏登记」，比文档漂移更严重；
4. 文档里点名但代码里不存在的能力，**判红**（正是 `source.*` 那类）。

## 为什么是「文档 + 代码」双查

只查一个方向会漏掉一半问题：只查「代码→文档」会放过 `source.*`；
只查「文档→代码」会放过那 17 条漏写的能力。两边都查才是闭环。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from omnicrawler.plugins.plugin_broker_contracts import (  # noqa: E402
    CAPABILITY_VERSIONS,
)

DOC = _ROOT / "docs" / "PLUGIN_CONTRACT.md"

#: **声明式能力**：只回答「宿主这一版支持不支持某个声明式特性」，本身不可调用。
#:
#: 它们出现在 `CAPABILITY_VERSIONS`（供 `required_capabilities` 做版本闸），
#: 但**没有** `_cap_*` 处理器、也不进能力表 —— 插件调它必然 `E_CONTRACT`。
#:
#: ★ 必须显式列在这里，不能靠"文档里没写"来默许 —— 那条路会让下面第 3 条判据
#:   变成空转。
_DECLARATION_ONLY = {
    "view.richtext": (
        "rich_text 组件是否可用（宿主渲染能力）。插件在 required_capabilities 里",
        "声明它来要求宿主版本；调用它没有任何语义。"
    ),
}

_CODE_TOKEN = re.compile(r"`([a-z][a-z0-9_.]*(?:/[a-z0-9_.]+)*)`")
#: 文档里凡是这样写的能力名（点分、被反引号包住）都算「被交代过」。
_PLAUSIBLE_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")


def _expand(token: str) -> list[str]:
    """把 `state.set/delete/migrate` 这类简写展开成完整能力名。"""
    parts = token.split("/")
    if len(parts) == 1:
        return parts
    head = parts[0]
    prefix = head.rsplit(".", 1)[0] if "." in head else ""
    expanded = [head]
    for suffix in parts[1:]:
        expanded.append(f"{prefix}.{suffix}" if prefix else suffix)
    return expanded


#: 能力表的表头行 —— 用它把「能力表」与文档里**其它**表格区分开。
#:
#: ★ 为什么必须区分：文档里还有一张「`source.seed` 载荷字段」表，
#:   里面点名 `source.file` / `source.files` / `source.input_file` ——
#:   那是**载荷字段名**，不是宿主能力。把它们当成能力会让这条门禁误报。
#:   同理 `source.seed` / `view.action` 是**插件要实现的操作**（`handle()` 收到），
#:   不是插件能调用的宿主能力。
_CAPABILITY_TABLE_HEADER = "| 能力 | 所需权限 | 说明 |"


def capability_table_rows(text: str) -> list[str]:
    """只取**能力表**那一段的表格行（到空行为止）。"""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == _CAPABILITY_TABLE_HEADER)
    except StopIteration:
        return []
    # 行**不含表头本身** —— 表头只有列名（「能力 / 所需权限 / 说明」），
    # 不是任何能力的声明。把它算进来会让调用方以为表里多了一行。
    rows: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    return rows


def documented_capabilities(text: str) -> set[str]:
    """文档**全文**里被反引号点名、且形如能力名的 token（已展开简写）。

    用于「代码 → 文档」方向：只要文档在任何地方交代过就算数（宽松是合理的，
    因为我们在问的是「作者能不能查到」）。
    """
    found: set[str] = set()
    for match in _CODE_TOKEN.finditer(text):
        for name in _expand(match.group(1)):
            if _PLAUSIBLE_CAPABILITY.match(name):
                found.add(name)
    return found


def claimed_capabilities(text: str) -> set[str]:
    """**能力表**里点名的能力（已展开简写）。

    用于「文档 → 代码」方向：能力表是「宿主承诺支持这些」的正式声明，
    表里写了而代码里没有 ⇒ 作者按表声明会被拒载。
    其它表格里的名字（载荷字段等）不算承诺。
    """
    found: set[str] = set()
    for row in capability_table_rows(text):
        for match in _CODE_TOKEN.finditer(row):
            for name in _expand(match.group(1)):
                if _PLAUSIBLE_CAPABILITY.match(name):
                    found.add(name)
    return found


def undocumented(caps: set[str], documented: set[str], declaration_only: set[str]) -> list[str]:
    """代码里有、文档里没交代的能力。"""
    return sorted(c for c in caps if c not in documented and c not in declaration_only)


def phantom(claimed: set[str], caps: set[str]) -> list[str]:
    """能力表里点名、代码里**不存在**的能力。

    只比对**能力表**（见 `claimed_capabilities`）—— 文档别处的载荷字段名、
    插件侧操作名不该被当成本模块要管的事。
    """
    return sorted(claimed - caps)


def mislabelled_declaration_only(
    declaration_only: set[str], handler_names: set[str]
) -> list[str]:
    """登记成「仅声明」但**其实有处理器**的能力（漏登记，比文档漂移更严重）。"""
    return sorted(declaration_only & handler_names)


def handler_capabilities(contracts_source: str) -> set[str]:
    """从 `_cap_xxx` 定义里还原出能力名（`_cap_a_b` ⇒ `a.b`）。

    处理器分散在 `plugin_broker*.py` 多个模块里，所以由调用方把源码拼起来传进来。
    """
    names = {
        match.group(1).replace("_", ".")
        for match in re.finditer(r"def _cap_([a-z0-9_]+)\(", contracts_source)
    }
    return names


def broker_sources() -> str:
    """把所有 `plugin_broker*.py` 的源码拼起来（处理器分散在多个 mixin 里）。"""
    parts = []
    for path in sorted((_ROOT / "src" / "omnicrawler" / "plugins").glob("plugin_broker*.py")):
        parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    del argv
    if not DOC.is_file():
        print(f"[FAIL] 找不到能力文档：{DOC}")
        return 1
    text = DOC.read_text(encoding="utf-8")
    documented = documented_capabilities(text)
    claimed = claimed_capabilities(text)
    caps = set(CAPABILITY_VERSIONS)
    if not caps:
        # ★ 空集合判红：`CAPABILITY_VERSIONS` 一夜之间变空时，"没有漏写"是恒真的假象。
        print("[FAIL] `CAPABILITY_VERSIONS` 是空的 —— 空集合不是通过")
        return 1

    problems: list[str] = []
    for name in undocumented(caps, documented, set(_DECLARATION_ONLY)):
        problems.append(
            f"能力 {name!r} 在代码里（CAPABILITY_VERSIONS）但文档没交代；"
            "插件作者看不到它，会按残缺的清单写插件。"
        )
    for name in phantom(claimed, caps):
        problems.append(
            f"文档点名了 {name!r}，但 `CAPABILITY_VERSIONS` 里没有它 —— "
            "作者按文档声明会被 `validate_required_capabilities` 当场拒载。"
        )
    handlers = handler_capabilities(broker_sources())
    for name in mislabelled_declaration_only(set(_DECLARATION_ONLY), handlers):
        problems.append(
            f"{name!r} 被登记成「仅声明」，但代码里**有** `_cap_*` 处理器 —— "
            "那它其实是可调用操作，应从 `_DECLARATION_ONLY` 移出并进能力表。"
        )
    for name in sorted(set(_DECLARATION_ONLY) - caps):
        problems.append(
            f"{name!r} 登记成「仅声明」，但 `CAPABILITY_VERSIONS` 里没有它 ——"
            "过期登记会让第 3 条判据空转。"
        )

    if problems:
        print(f"[FAIL] 能力表与代码不一致（{len(problems)} 处）：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(
        f"[OK] {len(caps)} 个能力全部在 docs/PLUGIN_CONTRACT.md 里有交代"
        f"（其中 {len(_DECLARATION_ONLY)} 个是显式登记的声明式能力）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
