"""校验工作区顶层目录与 `DIRECTORIES.md` / `DIRECTORIES.ignore` 一致。

## 这个门禁解决什么

根目录没有 `.git`，所以「哪些目录不该进仓库、能不能删」无法用
`.gitignore` 表达。结果是**每次有人（或 AI）进入工作区都要靠翻查
重新搞清楚**：这个 `market-plugin-and-template-development/` 是什么？
那个 `user-test-runs/` 能不能删？

本会话里我就为此花了十几分钟逐个盘点 —— 那是纯重复劳动。

现在把这件事变成**可校验的事实**：三向比对

1. 磁盘上的每个顶层条目 ⇒ 必须出现在清单里
2. 清单里的每个条目 ⇒ 必须在磁盘上存在（防止清单过期变成死文档）
3. 清单里的每个条目 ⇒ 必须出现在 `DIRECTORIES.md` 的表格里
   （防止「文档说得清楚、机器读的却是另一份」）

## 为什么值得单独一个门禁

「目录说明」很容易腐化成没人维护的死文档。而一旦它能判红，
它就变成了**活的契约**：新增目录不自说明，提交前就会失败。

## 用法

```powershell
python tools/gates/check_workspace_map.py            # 用默认工作区根
python tools/gates/check_workspace_map.py --root ..
```
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

#: 清单文件名（在**工作区根**，不是主仓内）。
IGNORE_NAME = "DIRECTORIES.ignore"

#: 文档文件名。
DOC_NAME = "DIRECTORIES.md"

#: 合法的类别值。
CATEGORIES = frozenset({"repo", "doc", "keep", "temp", "foreign"})

#: 合法的可删值。
REMOVABLE = frozenset({"yes", "no", "n/a"})


class Entry:
    """清单里的一行。"""

    def __init__(self, name: str, category: str, removable: str, note: str) -> None:
        self.name = name
        self.category = category
        self.removable = removable
        self.note = note

    def __repr__(self) -> str:
        return f"Entry({self.name!r}, {self.category!r}, {self.removable!r})"


def parse_ignore(path: Path) -> tuple[list[Entry], list[str]]:
    """解析清单。返回 `(条目, 格式问题)`。"""
    entries: list[Entry] = []
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [f"{path}: 无法读取（{exc}）"]

    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) != 4:
            problems.append(
                f"{path.name}:{number} 需要 4 个字段（名字|类别|可删|说明），实际 {len(parts)} 个"
            )
            continue
        name, category, removable, note = (item.strip() for item in parts)
        if not name:
            problems.append(f"{path.name}:{number} 名字为空")
            continue
        if category not in CATEGORIES:
            problems.append(
                f"{path.name}:{number} 类别 {category!r} 不合法（合法值：{sorted(CATEGORIES)}）"
            )
            continue
        if removable not in REMOVABLE:
            problems.append(
                f"{path.name}:{number} 可删值 {removable!r} 不合法（合法值：{sorted(REMOVABLE)}）"
            )
            continue
        if not note:
            problems.append(
                f"{path.name}:{number} 说明为空 —— 说不清用途的目录不该存在"
            )
            continue
        entries.append(Entry(name, category, removable, note))
    return entries, problems


def doc_mentions(doc_path: Path) -> set[str]:
    """从 `DIRECTORIES.md` 的表格里抽出**第一列**登记的名字。

    ★ 只取表格第一列，不取整行：正文里的 ``docs/``、``catalog.json``
    是**仓库内路径**，不是顶层条目，把它们当登记会误判。

    一格可能含多个用 `、` 分隔的名字（如 ``.pytest_cache/``、``.ruff_cache/``），
    逐个拆开。
    """
    try:
        text = doc_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # 拆表格行，丢掉首尾的空片段
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        if set(first) <= set("- :"):  # 分隔行 |---|---|
            continue
        for token in re.findall(r"`([^`]+)`", first):
            name = token.rstrip("/\\")
            # 一格里的多个名字用 `、` 分隔
            for part in re.split(r"[、,]", name):
                part = part.strip().rstrip("/\\")
                if part and " " not in part:
                    found.add(part)
    return found


def check(world_root: Path) -> list[str]:
    """返回问题列表（空＝通过）。"""
    problems: list[str] = []

    ignore_path = world_root / IGNORE_NAME
    doc_path = world_root / DOC_NAME

    if not ignore_path.is_file():
        return [f"缺少 {IGNORE_NAME}：工作区根需要一份机器可读的目录清单"]
    if not doc_path.is_file():
        return [f"缺少 {DOC_NAME}：工作区根需要一份人可读的目录地图"]

    entries, format_problems = parse_ignore(ignore_path)
    problems.extend(format_problems)

    declared = {entry.name for entry in entries}
    if len(declared) != len(entries):
        problems.append(f"{IGNORE_NAME} 有重复条目")

    # ── 1) 磁盘上的每个顶层条目都要登记 ───────────────────────────────
    try:
        on_disk = {item.name for item in world_root.iterdir()}
    except OSError as exc:
        return [f"无法枚举工作区根 {world_root}（{exc}）"]

    unregistered = sorted(on_disk - declared)
    if unregistered:
        problems.append(
            f"工作区根有未登记的条目：{unregistered} —— "
            f"请在 {IGNORE_NAME} 与 {DOC_NAME} 各加一行"
            "（说不清用途的，说明它还不该存在）"
        )

    # ── 2) 清单里的每个条目都要在磁盘上存在 ───────────────────────────
    missing = sorted(declared - on_disk)
    if missing:
        problems.append(
            f"{IGNORE_NAME} 登记了但磁盘上不存在：{missing} —— 清单已过期，删掉这些行"
        )

    # ── 3) 清单与文档必须一致 ──────────────────────────────────────────
    documented = doc_mentions(doc_path)
    undocumented = sorted(declared - documented)
    if undocumented:
        problems.append(
            f"{IGNORE_NAME} 里有但 {DOC_NAME} 表格里没写：{undocumented} —— "
            "机器读的和人读的必须是同一份事实"
        )
    # 文档正文里提到仓库内路径（`src/`、`catalog.json` 等）是**正常**的 ——
    # 本文件是人读的地图，不只列顶层条目。只有「看起来像顶层条目、
    # 却既不在清单也不在磁盘」的才算过期，判据是「不含路径分隔符」。
    suspects = {
        name
        for name in documented - declared - on_disk
        if "/" not in name and "\\" not in name and "<" not in name
    }
    if suspects:
        problems.append(
            f"{DOC_NAME} 提到了磁盘上不存在的顶层条目：{sorted(suspects)} —— 文档已过期"
        )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验工作区目录地图一致性")
    parser.add_argument(
        "--root",
        default="",
        help="工作区根（缺省＝主仓的上一级）",
    )
    args = parser.parse_args(argv)

    if args.root:
        world_root = Path(args.root).resolve()
    else:
        # 本文件在 <根>/OmniCrawler/tools/gates/ ⇒ 上三级是根
        world_root = Path(__file__).resolve().parents[3]

    problems = check(world_root)
    if problems:
        print(f"{BAD_MARK} 工作区目录地图不一致：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(f"{OK_MARK} 工作区目录地图一致（{world_root.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
