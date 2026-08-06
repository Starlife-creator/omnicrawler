from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from omnicrawl.core.logging_utils import configure_logging


@pytest.fixture
def clean_root() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    root.handlers.clear()
    for handler in before:
        root.addHandler(handler)


def test_configure_creates_file_handler_and_writes_log(
    clean_root: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "omnicrawl.core.logging_utils._log_file_path",
        lambda: tmp_path / "logs" / "omnicrawl.log",
    )
    configure_logging("INFO")
    handlers = logging.getLogger().handlers
    assert any(isinstance(handler, RotatingFileHandler) for handler in handlers)

    logging.getLogger("test.fixture").warning("hello file log")

    written = (tmp_path / "logs" / "omnicrawl.log").read_text(encoding="utf-8")
    assert "hello file log" in written


def test_no_stderr_falls_back_to_stringio(clean_root: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """打包后 pythonw.exe 无 stderr，configure_logging 不得抛异常。"""
    monkeypatch.setattr("omnicrawl.core.logging_utils._log_file_path", lambda: tmp_path / "logs" / "o.log")
    monkeypatch.delattr(sys, "__stderr__")
    configure_logging("INFO")  # 不应抛出 AttributeError/TypeError


def test_unknown_level_falls_back_to_info(clean_root: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """源B P2#64：非法日志级别不再 AttributeError，回退 INFO。"""
    monkeypatch.setattr("omnicrawl.core.logging_utils._log_file_path", lambda: tmp_path / "logs" / "o.log")
    configure_logging("VERBOSE")
    assert logging.getLogger().level == logging.INFO

    # 回退告警本身也要落到文件日志
    written = (tmp_path / "logs" / "o.log").read_text(encoding="utf-8")
    assert "VERBOSE" in written


def test_file_logging_can_be_disabled(clean_root: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omnicrawl.core.logging_utils._log_file_path", lambda: tmp_path / "logs" / "o.log")
    configure_logging("INFO", file_logging=False)
    handlers = logging.getLogger().handlers
    assert not any(isinstance(handler, RotatingFileHandler) for handler in handlers)
