"""`file_lock` 打不开锁文件时，错误必须**能照着做**。

为什么要有这个文件：原实现让 `PermissionError: [Errno 13] Permission denied: ...`
原样冒到界面。用户看到一行 errno，既不知道是「目录不可写」还是「被别的进程占着」，
也不知道该动什么。

实测（2026-11-24）两类真实成因：

1. **数据目录只读**（受限账户 / 便携包解压到 Program Files / 沙箱令牌只有 RX）；
2. **锁文件被另一个进程持有** —— Windows 上 `open("a+b")` 对已独占打开的文件
   也报 13（不是 32），所以光看 errno 分不出这两种。

现在用「同目录探针文件」区分：能建探针 ⇒ 目录可写 ⇒ 是占用；
连探针也建不了 ⇒ 目录不可写。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from omnicrawler.core.file_lock import file_lock


def test_normal_use_still_works(tmp_path: Path) -> None:
    """基本功能不能被诊断逻辑改坏。"""
    target = tmp_path / "sub" / ".lock"
    with file_lock(target, timeout=2):
        assert target.exists()


def test_error_message_names_the_directory_problem(tmp_path: Path, monkeypatch) -> None:
    """目录不可写时，提示必须说明「目录不可写」并给出动作。"""
    target = tmp_path / ".lock"

    real_open = Path.open

    def refuse_open(self, *args, **kwargs):
        if self == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    # 连探针文件也拒绝写，模拟「整个目录只读」
    real_write = Path.write_bytes

    def refuse_write(self, *args, **kwargs):
        if self.parent == target.parent:
            raise PermissionError(13, "Permission denied", str(self))
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_open)
    monkeypatch.setattr(Path, "write_bytes", refuse_write)
    with pytest.raises(PermissionError) as info:
        with file_lock(target, timeout=1):
            pass
    message = str(info.value)
    assert "不可写" in message, message
    assert "解压到可写目录" in message or "数据目录" in message, message


def test_error_message_mentions_the_other_process_when_dir_is_writable(tmp_path: Path, monkeypatch) -> None:
    """目录可写但文件打不开 ⇒ 提示「被占用」，而不是甩 errno。"""
    target = tmp_path / ".lock"
    real_open = Path.open

    def refuse_open(self, *args, **kwargs):
        if self == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_open)
    with pytest.raises(PermissionError) as info:
        with file_lock(target, timeout=1):
            pass
    message = str(info.value)
    assert "被占用" in message, message
    assert "另一个 OmniCrawler 进程" in message, message


def test_error_keeps_errno_and_filename(tmp_path: Path, monkeypatch) -> None:
    """CLI 的 PermissionError 分支会读 `filename`，不能丢。"""
    target = tmp_path / ".lock"
    real_open = Path.open

    def refuse_open(self, *args, **kwargs):
        if self == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_open)
    with pytest.raises(PermissionError) as info:
        with file_lock(target, timeout=1):
            pass
    assert info.value.errno == 13, "errno 丢了会让 str() 变成 [Errno None]"
    assert info.value.filename == str(target)


def test_busy_lock_still_times_out_with_message(tmp_path: Path) -> None:
    """文件能打开但锁拿不到时，仍是带自定义消息的 TimeoutError。

    **必须用子进程持有锁**：Windows 的字节范围锁是**按进程**的，
    同进程内对同一文件再次 `locking` 会直接成功（实测），测不出争用。
    """
    if os.name != "nt":
        pytest.skip("该用例靠 Windows 的独占锁语义")
    import subprocess
    import sys
    import textwrap

    target = tmp_path / ".lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import msvcrt, pathlib, time
            p = pathlib.Path(r"{target}")
            p.parent.mkdir(parents=True, exist_ok=True)
            f = p.open("a+b")
            f.write(b"\\0"); f.flush(); f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            print("locked", flush=True)
            time.sleep(30)
        """)],
        stdout=subprocess.PIPE,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == b"locked", "子进程没拿到锁"
        with pytest.raises(TimeoutError, match="正在使用"):
            with file_lock(target, timeout=0.5, busy_message="组件注册表正在使用"):
                pass
    finally:
        holder.kill()
        holder.wait(timeout=10)
