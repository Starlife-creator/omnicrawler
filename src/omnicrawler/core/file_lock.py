"""Bounded OS file locks, released by the kernel when a process exits."""

from __future__ import annotations

import importlib
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _diagnose_open_failure(path: Path, exc: OSError) -> OSError:
    """把「打不开锁文件」升级成**能照着做**的提示。

    为什么需要：`file_lock` 抛的是裸 `PermissionError: [Errno 13] ...`，
    而它经 `components` / `components install` 冒到界面上时，用户只看到一行
    `[Errno 13] Permission denied: .../<lock>` ——
    既不知道是「目录不可写」还是「被别的进程占着」，也不知道该动什么。

    实测（2026-11-24）两类真实成因：

    1. **数据目录只读**（受限账户 / 便携包解压到 Program Files / 沙箱令牌只有 RX）；
    2. **锁文件被另一个进程持有**——Windows 上 `open("a+b")` 对已独占打开的文件
       也报 13（不是 32），所以光看 errno 分不出这两种。

    这里用一个**同目录探针文件**区分它们：能建探针 ⇒ 目录可写 ⇒ 是占用；
    连探针也建不了 ⇒ 目录不可写。提示里给出对应动作。
    """
    probe = path.with_name(f".{path.name}.write-probe")
    writable = False
    try:
        probe.write_bytes(b"")
        writable = True
    except OSError:
        writable = False
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    if writable:
        detail = (
            f"无法打开锁文件 {path}：该目录可写，但文件被占用。"
            "通常意味着另一个 OmniCrawler 进程正在写组件注册表；"
            "请关闭其它实例后重试。"
        )
    else:
        detail = (
            f"无法打开锁文件 {path}：所在目录不可写。"
            "若程序装在只读位置（Program Files / 只读介质），请解压到可写目录，"
            "或把数据目录改到用户目录。"
        )
    # 用 (errno, strerror, filename) 三元组构造，才与 OSError 的语义一致：
    # `PermissionError(msg)` 会留下 `errno=None`，str() 里出现「[Errno None] None」，
    # 反而比原始错误更难读。
    return PermissionError(exc.errno or 13, detail, str(path))


@contextmanager
def file_lock(path: Path, *, timeout: float = 10.0, busy_message: str = "资源正在使用，请稍后重试") -> Iterator[None]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
    except PermissionError as exc:
        raise _diagnose_open_failure(path, exc) from exc
    with stream:
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    windows_lock = importlib.import_module("msvcrt")
                    windows_lock.locking(stream.fileno(), windows_lock.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(busy_message) from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                windows_lock = importlib.import_module("msvcrt")
                windows_lock.locking(stream.fileno(), windows_lock.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
