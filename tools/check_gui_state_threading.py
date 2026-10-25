#!/usr/bin/env python3
"""C-#13：UI 层直连 ``StateStore`` 点位白名单门禁（只降不升）。

规则口径（详见 ``docs/GUI_STATE_LOADING.md``）：

* ``StateStore(...)`` 的唯一合法封装点是 ``gui/core/state_query_service.py`` 薄适配层；
  后台 worker 通过该模块读取数据，结果经信号回投 UI 线程。
* 迁移完成前，三个已测量的视图文件允许保留既有直连点位，数量列入基线
  ``BASELINE_STATE_SITES``，只降不升；**新文件不得新增直连**，基线内文件点位
  数量增长即判红。
* 本门禁只做静态点位约束，不替代负载重测；数据库规模变化后按架构文档先测量、
  再决定是否迁入后台线程。

用法::

    python tools/check_gui_state_threading.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUI_VIEWS = PROJECT_ROOT / "src" / "omnicrawler" / "gui" / "views"
GUI_CORE = PROJECT_ROOT / "src" / "omnicrawler" / "gui" / "core"

#: 允许封装 StateStore 的适配层（其内部查询函数由后台/同步调用点复用）。
SANCTIONED_WRAPPER = Path(
    "src/omnicrawler/gui/core/state_query_service.py"
).as_posix()

#: 迁移前已测量的直连点位基线（相对仓库路径 -> 允许的最大直连数量）。
BASELINE_STATE_SITES: dict[str, int] = {
    "src/omnicrawler/gui/views/developer_inspector.py": 2,
    "src/omnicrawler/gui/views/project_dialogs.py": 1,
    "src/omnicrawler/gui/views/reprocess_review.py": 2,
}

_STATESTORE_CALL = re.compile(r"\bStateStore\s*\(")


def _count_direct_sites(path: Path) -> int:
    return len(_STATESTORE_CALL.findall(path.read_text(encoding="utf-8")))


def check(root: Path = PROJECT_ROOT) -> list[str]:
    issues: list[str] = []
    scanned: list[Path] = []
    for directory in (root / "src" / "omnicrawler" / "gui" / "views",
                      root / "src" / "omnicrawler" / "gui" / "core"):
        if directory.is_dir():
            scanned.extend(sorted(directory.rglob("*.py")))

    for path in scanned:
        rel = path.relative_to(root).as_posix()
        if rel == SANCTIONED_WRAPPER:
            continue
        count = _count_direct_sites(path)
        if count == 0:
            continue
        limit = BASELINE_STATE_SITES.get(rel)
        if limit is None:
            issues.append(
                f"{rel}: 新增 {count} 处 UI 层直连 StateStore；应经 "
                "gui/core/state_query_service.py 适配层（C-#13）"
            )
        elif count > limit:
            issues.append(
                f"{rel}: 直连 StateStore 点位 {count} 处，超出基线 {limit} 处（C-#13 只降不升）"
            )
    return issues


def main() -> int:
    issues = check(PROJECT_ROOT)
    if issues:
        print("GUI 状态库线程约束检查失败：", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("GUI 状态库线程约束检查通过（直连点位未超出已测量基线）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
