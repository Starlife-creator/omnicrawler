"""worker 套接字路径：**深工作区也必须能起**（W1.1，2026-09-15）。

## 背景（实测）

`LocalWorkerBackend` 原先把 AF_UNIX 套接字放在**工作区**里：`<workspace>/.worker-<32hex>.sock`。
POSIX `sun_path` 上限是 **Linux 108 / macOS 104 字节（含结尾 NUL）** ⇒ 工作区一深就超限，
worker **起不来**，报 `RuntimeError: 本地Worker启动超时: AF_UNIX path too long`——
CI 上 ubuntu 与 macOS 都能复现（挂掉 4 条 GUI 端到端），属**产品缺陷**，不是测试环境问题。

修法：套接字改到**系统临时区的短路径**下（`/tmp` 或 gettempdir，取第一个满足预算的），
名字用会话号前 16 位（够唯一），目录 0700、套接字 0600；认证仍由 `multiprocessing` 的 `authkey` 承担。
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

from omnicrawler.runtime.execution_backend import (
    UNIX_SOCKET_PATH_BUDGET,
    _prepare_socket_dir,
    _worker_address,
    _worker_socket_path,
)

#: 深工作区：模拟 CI（pytest 的 tmp_path 很长）或用户把项目放在深层目录
DEEP_WORKSPACE = Path("/very/deep/workspace") / ("x" * 120) / "project"


def test_old_layout_would_exceed_the_sun_path_limit() -> None:
    """先把问题钉住：旧布局（套接字放工作区）在深工作区下**确实超限**。

    这条不是"测旧代码"，而是证明本模块存在的必要性：如果哪天有人把套接字挪回工作区，
    这个算术会立刻说明后果（而不是等 CI 上 worker 起不来）。
    """
    legacy = DEEP_WORKSPACE / f".worker-{uuid.uuid4().hex}.sock"
    assert len(str(legacy).encode("utf-8")) > 104, "深工作区下旧布局应超 macOS 上限"


def test_socket_path_fits_the_budget_even_for_deep_workspaces() -> None:
    """**核心不变量**：无论工作区多深，套接字路径都在预算内。

    用真实的系统临时区做根（显式传 roots 是为了可测；pytest 的 `tmp_path` 本身就深，
    在 Windows 上会超预算 ⇒ 那种情况应由"回退"用例覆盖，而不是这里）。
    """
    root = Path(tempfile.gettempdir())
    path = _worker_socket_path(DEEP_WORKSPACE, uuid.uuid4().hex, roots=(root,))
    assert len(str(path).encode("utf-8")) <= UNIX_SOCKET_PATH_BUDGET, str(path)
    assert DEEP_WORKSPACE not in path.parents, f"套接字不应落在工作区里：{path}"
    assert path.parent == root / "omnicrawler"
    assert path.name.endswith(".sock")


def test_socket_path_is_deterministic_and_session_unique(tmp_path: Path) -> None:
    """同一 (工作区, 会话号) 恒定；换会话号必须换路径（否则并发任务会互相顶掉）。"""
    session = uuid.uuid4().hex
    roots = (tmp_path,)
    assert _worker_socket_path(DEEP_WORKSPACE, session, roots=roots) == _worker_socket_path(
        DEEP_WORKSPACE, session, roots=roots
    )
    assert _worker_socket_path(DEEP_WORKSPACE, uuid.uuid4().hex, roots=roots) != _worker_socket_path(
        DEEP_WORKSPACE, session, roots=roots
    )


def test_socket_path_falls_back_to_workspace_when_no_root_fits() -> None:
    """候选根都放不下时退回工作区——**不抛异常**，让失败信息保持可诊断（旧行为）。"""
    tiny_root = Path("/") / ("y" * 200)
    path = _worker_socket_path(DEEP_WORKSPACE, "a" * 32, roots=(tiny_root,))
    assert path.parent == DEEP_WORKSPACE
    assert path.name.startswith(".worker-")


@pytest.mark.skipif(os.name == "nt", reason="POSIX 文件权限语义")
def test_socket_dir_is_private(tmp_path: Path) -> None:
    """套接字目录必须 0700：同机其它用户不得连上（authkey 是第二道，不该是唯一一道）。"""
    path = tmp_path / "omnicrawler" / "abc.sock"
    _prepare_socket_dir(path)
    assert path.parent.is_dir()
    assert (path.parent.stat().st_mode & 0o777) == 0o700


def test_windows_branch_uses_named_pipe_independent_of_workspace() -> None:
    """Windows 走命名管道：与工作区长度无关，且家族字符串必须是后端认识的那个。"""
    session = uuid.uuid4().hex
    family, address = _worker_address(DEEP_WORKSPACE, session, is_windows=True)
    assert family == "AF_PIPE"
    assert address == rf"\\.\pipe\omnicrawler-{session}"


@pytest.mark.skipif(os.name == "nt", reason="AF_UNIX 路径长度是 POSIX 概念")
def test_posix_branch_returns_short_unix_address(tmp_path: Path) -> None:
    session = uuid.uuid4().hex
    family, address = _worker_address(DEEP_WORKSPACE, session)
    assert family == "AF_UNIX"
    assert len(address.encode("utf-8")) <= UNIX_SOCKET_PATH_BUDGET
    # 目录已按需建好（后端启动前就能连），且是本用户私有
    assert Path(address).parent.is_dir()
    assert (Path(address).parent.stat().st_mode & 0o777) == 0o700
