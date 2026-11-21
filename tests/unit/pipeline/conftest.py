"""pipeline 测试的全局隔离（`work/` 共享目录）。

## 为什么需要它

这组测试的 task.yaml 里写的是 `project: {workspace: work}` —— **相对路径**。
它解析到**项目根**的 `work/`，于是同一进程内所有 pipeline 测试
**共用同一个 `state.sqlite3`**：

* 前一个测试写入的 records 留在库里，后一个拿到别人的记录，报
  `ValueError: Record does not belong to the requested run`；
* frontier 队列跨测试累积，`frontier_pending` 期望 0 却得到 1。

实测（2026-11-21）原始状态下 `tests/unit/pipeline/` 有 **12 条失败**，
而每条单独跑都能过 —— 典型的顺序依赖。

## 为什么不用 chdir

试过 `monkeypatch.chdir(tmp_path)`：无效。`Pipeline` 在 **config 加载时**
就把 workspace 解析成绝对路径并缓存，chdir 发生在之后再改已经晚了。

## 为什么删目录本身而不是清内容

有测试用 `config.workspace.mkdir(parents=True)`（**没有 `exist_ok`**），
目录残留会让它报 `FileExistsError`。所以整体删掉再重建。

`.gitkeep` 要放回去 —— 它让 `work/` 在 git 里存在。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

#: 项目根（`tests/unit/pipeline/` 往上三级）。
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: 测试共用的工作目录。
_WORK = _PROJECT_ROOT / "work"


def remove_work_dir() -> None:
    """删掉 `work/` 整个目录。

    **不能重建**：有测试用 `config.workspace.mkdir(parents=True)`
    （没有 `exist_ok`），目录存在就会报 `FileExistsError`。
    `.gitkeep` 由 session 结束时的 fixture 恢复（见 `_restore_work_dir`）。
    """
    if _WORK.is_dir():
        shutil.rmtree(_WORK, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _restore_work_dir() -> None:
    """session 结束后把 `work/.gitkeep` 放回去（仓库依赖这个空目录）。"""
    yield
    try:
        _WORK.mkdir(parents=True, exist_ok=True)
        keep = _WORK / ".gitkeep"
        if not keep.is_file():
            keep.write_bytes(b"")
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _isolate_shared_workspace() -> None:
    """每个测试前后重置共享的 `work/`。"""
    remove_work_dir()
    yield
    remove_work_dir()
