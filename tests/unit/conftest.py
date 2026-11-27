"""`tests/unit/` 共用判据与隔离。

## 一、文档工作台可安装性判据

2026-11-18 的 `817bccc5` 把 PDF / convertx / document_ir **整体迁出**为
可选插件 `document-workbench`。于是主仓里依赖这些能力的测试出现两种情况：

* **能力缺失导致的失败** —— `omnicrawler pdf --help` 只输出「插件未安装」提示，
  退出码 1，而测试断言的是插件的帮助文本；
* **能力缺失被误当成缺陷** —— 只看红绿会以为代码坏了，实际是可选依赖未装。

这类测试正确做法是 **skip**，不是改断言迁就缺失，也不是删掉测试。
判据用 `document_capability()` —— 那是 CLI 自己判断能力的同一个入口，
不会出现「测试以为装了、CLI 以为没装」的分歧。

市场安装**只有 GUI 入口、没有 CLI**，测试环境无法在一条命令里准备好它。
将来若有 CLI 安装路径，这个 skip 会自动消失（判据随之变真）。

## 二、`work/` 共享目录隔离

多个测试的 task.yaml 写的是 `project: {workspace: work}` —— **相对路径**，
解析到**项目根**的 `work/`。于是同一进程内的测试共用一个工作区：

* `state.sqlite3` 里的 records 跨测试累积 → `Record does not belong to the requested run`
* `.repair-active.json` 残留 → `工作区修复记录属于其他配置`
* 登录态目录残留 → `reset_login()["moved"]` 期望 0 却得到 3

**特征**：单独跑能过、整目录跑就失败。

### 为什么不用 chdir

试过 `monkeypatch.chdir(tmp_path)`：无效。`Pipeline` 在 **config 加载时**
就把 workspace 解析成绝对路径并缓存，chdir 发生在之后再改已经晚了。

### 为什么删目录本身而不是清内容

有测试用 `config.workspace.mkdir(parents=True)`（**没有 `exist_ok`**），
目录残留会报 `FileExistsError`。所以整体删掉，session 结束时恢复 `.gitkeep`。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

#: 项目根（`tests/unit/` 往上两级）。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 测试共用的工作目录。
_WORK = _PROJECT_ROOT / "work"


def document_workbench_available() -> bool:
    """文档工作台插件是否真的可用（供 `capability` 判据使用）。"""
    try:
        from omnicrawler.services.document_facade import document_capability

        return bool((document_capability() or {}).get("available"))
    except Exception:  # noqa: BLE001 - 判定失败时按不可用处理
        return False


#: 未装文档工作台插件时跳过（用于依赖 pdf / convertx / document_ir 的测试）。
requires_document_workbench = pytest.mark.skipif(
    not document_workbench_available(),
    reason="document-workbench 插件未安装：该能力已迁出为可选插件",
)


def remove_work_dir() -> None:
    """删掉 `work/` 整个目录（不重建 —— 有测试要求它不存在）。"""
    if _WORK.is_dir():
        shutil.rmtree(_WORK, ignore_errors=True)


def _ensure_work_gitkeep() -> None:
    """把 `work/.gitkeep` 放回去（仓库依赖这个 0 字节占位）。

    ★ 幂等且失败静默：这是**清理**动作，不该因为权限/并发而让测试失败。
    """
    # ★ 用**标准库 os + 内建 open**，不用 `Path.mkdir` / `Path.write_bytes`：
    #   有测试会把 `pathlib` 的方法换成"读追踪器"（`test_build_common` 的
    #   `test_manifest_does_not_read_whole_files_into_memory` 就是），
    #   那时 `Path.write_bytes` 会抛 `AttributeError: _Tracked has no attribute write`，
    #   而这是**清理动作**，绝不能反过来让测试失败。
    try:
        os.makedirs(str(_WORK), exist_ok=True)
        target = os.path.join(str(_WORK), ".gitkeep")
        if not os.path.isfile(target):
            with open(target, "wb"):
                pass
    except Exception:  # noqa: BLE001 - 清理失败不得影响测试结果
        pass


@pytest.fixture(scope="session", autouse=True)
def _restore_work_dir_after_session() -> None:
    """session 结束后把 `work/.gitkeep` 放回去（仓库依赖这个空目录）。"""
    yield
    _ensure_work_gitkeep()


@pytest.fixture(autouse=True)
def _isolate_shared_workspace() -> None:
    """每个测试前后重置共享的 `work/`。

    autouse：这组测试全部依赖它，逐个声明容易漏。
    """
    remove_work_dir()
    yield
    remove_work_dir()
    # ★ 2026-11-27：**每个测试**结束后都要把 `.gitkeep` 放回去，不能只等 session 结束。
    #
    #   实测事故：某次在"测试刚删掉 work/、还没到 session 结束"的窗口里跑了
    #   `git add -A` ⇒ 把 `work/.gitkeep` 的**删除**一起提交了 ⇒ CI 的
    #   `check_test_cleanliness` 判红（它看到工作区里 `.gitkeep` 是删除状态）。
    #   这两次提交都是"我的操作时机"造成的，不是产品缺陷；但从"仓库依赖这个空占位"
    #   这条约束看，**缩窄不一致窗口**才是真修法。
    _ensure_work_gitkeep()
