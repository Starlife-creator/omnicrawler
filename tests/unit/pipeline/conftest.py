"""pipeline 测试的全局隔离。

## 为什么需要它

这组测试的 `task.yaml` 里写的是 `project: {workspace: work}` —— **相对路径**。
相对路径会解析到**项目根**的 `work/`，于是同一进程内所有 pipeline 测试
**共用同一个 `state.sqlite3`**。

后果是测试之间互相污染：

* 前一个测试写入的 records 留在库里，后一个测试拿到别人的记录，
  报 `ValueError: Record does not belong to the requested run`；
* frontier 队列跨测试累积，`frontier_pending` 期望 0 却得到 1。

实测（2026-11-21）原始状态下 `tests/unit/pipeline/` 有 **12 条失败**，
而每条单独跑都能过 —— 典型的顺序依赖。

## 为什么用「清空目录」而不是 chdir

试过 `monkeypatch.chdir(tmp_path)`：无效。因为 `Pipeline` 在
**config 加载时**就把 workspace 解析成绝对路径并缓存，chdir 发生在
之后再改已经晚了。

清空项目内 `work/` 是对症的做法：它保证每个测试从**空库**开始，
与 `workspace: work` 的相对路径语义不冲突。

## 边界

只清 `work/` 下**测试产物**，保留 `.gitkeep`（它让目录在 git 里存在）。
不会碰项目其它任何目录。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

#: 项目根（`tests/unit/pipeline/` 往上三级）。
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: 测试共用的工作目录。
_WORK = _PROJECT_ROOT / "work"


def _clear_work_dir() -> None:
    """清空 `work/` 下的测试产物，保留 `.gitkeep`。"""
    if not _WORK.is_dir():
        return
    for entry in _WORK.iterdir():
        if entry.name == ".gitkeep":
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
        except OSError:
            # 被占用/权限不足就跳过 —— 隔离失败不该让测试报错，
            # 否则会把「测试环境问题」伪装成「业务断言失败」。
            continue


@pytest.fixture(autouse=True)
def _isolate_shared_workspace() -> None:
    """每个 pipeline 测试前清空共享的 `work/`。

    autouse：这组测试全部依赖它，逐个声明容易漏。
    """
    _clear_work_dir()
    yield
    _clear_work_dir()
