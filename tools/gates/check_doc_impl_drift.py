"""文档与实现一致性：`优化方案.md` 说「未做」的项，代码里不该已经实现。

## 这个门禁解决什么

2026-11-22 的核对发现：`优化方案.md` 有 **5 处**写着「尚未开始」「界面未做」
「未实现」，而代码里**全部已完成**：

| 文档说 | 实际 |
|---|---|
| `kind: native` 执行器「尚未开始」 | `build_native_spec` 已实现 |
| GUI 内核界面「未做」 | `gui/views/browser_kernels.py` |
| 失败驱动换内核「未做」 | `browser_kernel_advice.py` + 23 条守卫 |
| 登录态提示「需 GUI 配合」 | `QMessageBox` 已接入 |
| Schema v2 `platform_matrix`「需三边改」 | `_validate_platform_matrix` |

**为什么这件事反复发生**：实现做完了，文档还停在旧状态。
而「未做」这个词**不会主动报警** —— 它只会在下一次核对时被发现。

## 判据：锚点探测

对每条登记在 `_WATCHED` 里的「文档说法 ↔ 代码证据」配对：

* 文档里出现该说法（关键词），**且**
* 代码里出现该证据（符号/文件）

两者同时成立 ⇒ 判红，指名是哪一条需要更新。

## 为什么只登记「已核对过的」配对

自动推断「这句话对应哪段代码」做不到（自然语言 → 符号需要语义理解）。
所以本门禁守的是**已发现并修过的那些**：防止它们回退，
也强制新发现的同类问题登记进来。

## 边界（如实说明）

它**发现不了新的**「文档滞后」——只有登记过的配对才被检查。
新发现的滞后需要人工登记到 `_WATCHED`。这是刻意的：
宁可只守确定的，也不做会大量误报的模糊推断。

## 用法

```powershell
python tools/gates/check_doc_impl_drift.py
```
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

#: 文档路径（工作区根，不在仓库内）。
_DOC = "../优化方案.md"

#: 已核对过的配对：`(文档说法关键词, 代码证据文件, 代码证据符号)`。
#:
#: 三者同时成立 ⇒ 文档说的是「未做」但代码已有 ⇒ 判红。
_WATCHED: tuple[tuple[str, str, str], ...] = (
    ("kind: native` 执行器", "未实现", "src/omnicrawler/services/component_runner.py", "def build_native_spec"),
    ("GUI 界面", "未做", "src/omnicrawler/gui/views/browser_kernels.py", "class BrowserKernelPanel"),
    ("失败驱动的「换内核试试」", "未做", "src/omnicrawler/services/browser_kernel_advice.py", "def advise_switch"),
    ("内核切换的持久化", "未做", "src/omnicrawler/gui/views/browser_kernels.py", "def _restore_preference"),
)


def _doc_text(root: Path) -> str | None:
    path = (root / _DOC).resolve()
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查文档说「未做」但代码已实现的项目",
    )
    parser.add_argument("--root", default="", help="仓库根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    doc = _doc_text(root)

    # 文档不在（如 CI 单仓 checkout）⇒ 跳过，不判红。
    if doc is None:
        print(f"  跳过：找不到 {_DOC}（工作区根不可达，CI 单仓场景）")
        return 0

    problems: list[str] = []
    checked = 0
    for keyword, pending_mark, evidence_file, evidence_symbol in _WATCHED:
        # **匹配的是「同一行里既有该关键词、又有未做标记」**。
        #
        # 早先我把 claim 写成「内核切换的持久化** 未做」这种拼接串，
        # 与文档实际排版对不上 —— 四组配对**一条都没匹配上**，
        # 门禁是假通过（反向断言当场暴露）。
        #
        # 改为按行判断：表格行的表头含关键词，且该行含未做标记。
        plain_doc = doc.replace("`", "").replace("*", "")
        matched = False
        for line in plain_doc.splitlines():
            if not line.strip().startswith("|"):
                continue
            if keyword in line and pending_mark in line:
                matched = True
                break
        if not matched:
            continue
        checked += 1
        # 代码里已经有证据了吗？
        evidence_path = root / evidence_file
        if not evidence_path.is_file():
            continue
        try:
            code = evidence_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if evidence_symbol in code:
            problems.append(
                f"文档仍说「{keyword}」{pending_mark}，但 {evidence_file} 里已有 "
                f"`{evidence_symbol}`"
            )

    if problems:
        print(f"{BAD_MARK} 文档滞后于实现：")
        for item in problems:
            print(f"  - {item}")
        print(
            "\n实现已经完成，文档却还写着「未做」。请更新对应的状态行，"
            "并写清证据（文件 + 符号 + 实测结果）。"
        )
        return 1

    print(f"{OK_MARK} 已核对的 {len(_WATCHED)} 组「文档 ↔ 实现」无滞后")
    return 0


if __name__ == "__main__":
    sys.exit(main())
