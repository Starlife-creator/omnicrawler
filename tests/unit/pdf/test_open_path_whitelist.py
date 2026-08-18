"""B07-001：open_path 扩展名白名单回归测试。

Windows os.startfile 按扩展名关联执行；白名单拒绝可执行件，防输出目录
混入非预期文件被双击/自动执行。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from omnicrawler.pdfx.desktop import open_path


def test_open_path_rejects_executable_extension(tmp_path: Path) -> None:
    evil = tmp_path / "payload.exe"
    evil.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="白名单"):
        open_path(evil)


def test_open_path_rejects_unknown_extension(tmp_path: Path) -> None:
    weird = tmp_path / "data.dat"
    weird.write_bytes(b"x")
    with pytest.raises(ValueError, match="白名单"):
        open_path(weird)


def test_open_path_allows_directory(tmp_path: Path) -> None:
    """目录放行（文件管理器中打开安全）。"""
    if sys.platform == "win32":
        pytest.skip("Windows os.startfile 实际拉起资源管理器，跳过")
    # 非 Windows：Popen 命令不真正执行（mock），仅验证不抛白名单异常
    from unittest.mock import patch

    with patch("omnicrawler.pdfx.desktop.subprocess.Popen") as popen:
        open_path(tmp_path)
        assert popen.called


def test_open_path_allows_openable_extension(tmp_path: Path) -> None:
    ok = tmp_path / "report.pdf"
    ok.write_bytes(b"%PDF")
    if sys.platform == "win32":
        pytest.skip("Windows os.startfile 实际拉起关联程序，跳过")
    from unittest.mock import patch

    with patch("omnicrawler.pdfx.desktop.subprocess.Popen") as popen:
        open_path(ok)
        assert popen.called
