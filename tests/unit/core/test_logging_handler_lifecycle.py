"""configure_logging 的 handler 生命周期：重配必须关闭旧句柄。

为什么要有这个文件：`RotatingFileHandler` 在**构造时**就打开文件，
而原来的重配逻辑是 `root.handlers.clear()` —— 那**只丢引用、不关文件**。

实测（2026-11-24）：连续调用三次后，前两次的 `stream` 仍 `closed=False`，
也就是每重配一次泄漏一个文件句柄。桌面端每切一次日志级别 / 改一次配置都会
调到这里；Windows 上被占用的日志文件还会挡住轮转与删除。

本文件钉住：重配后旧 handler **被关闭**，且日志功能本身不受影响。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from omnicrawler.core import logging_utils as lu


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把日志落点指到临时目录（默认落点在用户目录，沙箱里可能不可写）。"""
    target = tmp_path / "logs" / "omnicrawler.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(lu, "_log_file_path", lambda: target)
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    try:
        yield target
    finally:
        # 还原，避免污染同进程里的其它用例
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved:
            root.addHandler(handler)
        root.setLevel(saved_level)


def _file_handlers() -> list[logging.FileHandler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]


def test_reconfigure_closes_the_previous_file_handler(log_dir: Path) -> None:
    """重配后旧 handler 的文件必须被关掉 —— 这是防句柄泄漏的关键。"""
    lu.configure_logging("INFO")
    first = _file_handlers()
    assert len(first) == 1 and first[0].stream is not None

    lu.configure_logging("INFO")
    assert first[0].stream is None, "旧 handler 未关闭 ⇒ 文件句柄泄漏"
    assert len(_file_handlers()) == 1, "重配后应只剩一个文件 handler"


def test_repeated_reconfigure_does_not_accumulate_handlers(log_dir: Path) -> None:
    """连续重配多次，文件 handler 数量必须恒为 1。"""
    seen: list[logging.FileHandler] = []
    for _ in range(5):
        lu.configure_logging("INFO")
        current = _file_handlers()
        assert len(current) == 1
        seen.extend(current)

    # 除最后一个外，全部应已关闭
    still_open = [h for h in seen[:-1] if h.stream is not None]
    assert not still_open, f"{len(still_open)} 个旧 handler 仍持有打开的文件"


def test_logging_still_works_after_reconfigure(log_dir: Path) -> None:
    """关句柄不能把日志本身关坏。"""
    lu.configure_logging("INFO")
    lu.configure_logging("INFO")
    logging.getLogger("probe").info("after-reconfigure")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "after-reconfigure" in log_dir.read_text(encoding="utf-8")


def test_level_is_applied_on_each_reconfigure(log_dir: Path) -> None:
    """重配要真的改级别 —— 别为了关句柄把其它语义也改掉。"""
    lu.configure_logging("INFO")
    assert logging.getLogger().level == logging.INFO
    lu.configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_unknown_level_falls_back_without_leaking(log_dir: Path) -> None:
    """未知级别回退 INFO，且同样不泄漏句柄。"""
    lu.configure_logging("NOT-A-LEVEL")
    assert logging.getLogger().level == logging.INFO
    lu.configure_logging("NOT-A-LEVEL")
    assert len(_file_handlers()) == 1
