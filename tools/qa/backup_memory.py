#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""把工作区 `memory/` 快照到 `local-backups/`（**仓库外的应急副本**）。

## 为什么需要这个工具

`memory/` 刻意**不进任何 git 仓库**（它是跨会话的私有工作记忆）。代价是：
**没有任何回滚能力**。

2026-11-27 一天之内我两次用「读出全文 → 拼字符串 → 整个写回」编辑它：

| 文件 | 事故前 | 事故后 | 可恢复 |
|---|---|---|---|
| `RULED_OUT.md` | 949 行 | 47 行 | 只靠我上下文里读过的三节，其余永久丢失 |
| `PAIN_POINTS.md` | 100 739 字节 | 52 806 字节 | 同上，约 1259 行永久丢失 |

根因之一很隐蔽：`read` 对大文件**有返回上限**（`totalLines` 报的比 `lines` 多），
拿 `lines` 拼回全文就会静默丢弃超限部分。

## 判据

快照必须是**完整拷贝**，不是增量、不是压缩、不改写任何字节 ——
恢复时要能直接 `copy` 回去。因此这里用 `shutil.copytree` 而非任何重写。

★ 快照目录名带 UTC 时间戳，**永不覆盖**已有快照（本项目已多次因"覆盖式备份"
  失去对照基线）。

## 用法

```powershell
python tools/qa/backup_memory.py            # 建一份快照
python tools/qa/backup_memory.py --list     # 看现有快照
python tools/qa/backup_memory.py --restore <目录名>   # 从快照恢复（先自动再备份当前）
```
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MEMORY = _ROOT.parent / "memory"
_BACKUPS = _ROOT.parent / "local-backups"


def snapshot(memory: Path = _MEMORY, backups: Path = _BACKUPS) -> Path:
    """建一份带时间戳的完整快照，返回目标目录。"""
    if not memory.is_dir():
        raise FileNotFoundError(f"memory 目录不存在: {memory}")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = backups / f"memory-snapshot-{stamp}"
    if target.exists():
        # 同一秒内重复调用：加后缀而不是覆盖
        index = 2
        while (backups / f"memory-snapshot-{stamp}-{index}").exists():
            index += 1
        target = backups / f"memory-snapshot-{stamp}-{index}"
    backups.mkdir(parents=True, exist_ok=True)
    shutil.copytree(memory, target)
    return target


def _dir_stats(path: Path) -> tuple[int, int]:
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="快照 / 恢复工作区 memory 目录")
    parser.add_argument("--list", action="store_true", help="列出已有快照")
    parser.add_argument("--restore", default="", help="从指定快照目录名恢复")
    args = parser.parse_args(argv)

    if args.list:
        if not _BACKUPS.is_dir():
            print("尚无快照目录")
            return 0
        found = sorted(p for p in _BACKUPS.iterdir() if p.name.startswith("memory-snapshot-"))
        if not found:
            print("尚无快照")
            return 0
        for path in found:
            count, total = _dir_stats(path)
            print(f"  {path.name}  {count} 文件  {total} 字节")
        return 0

    if args.restore:
        source = _BACKUPS / args.restore
        if not source.is_dir():
            print(f"FAIL 找不到快照: {source}", file=sys.stderr)
            return 1
        # 恢复前先把**当前**状态也备份掉 —— 否则「恢复」本身又是一次不可逆操作
        safety = snapshot()
        print(f"[恢复] 当前状态已先备份到 {safety.name}")
        for item in source.iterdir():
            if item.is_file():
                shutil.copy2(item, _MEMORY / item.name)
        print(f"[恢复] 已从 {args.restore} 恢复 {len(list(source.iterdir()))} 个文件")
        return 0

    target = snapshot()
    count, total = _dir_stats(target)
    print(f"[OK] 快照完成: {target.name}（{count} 文件，{total} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
