"""Phase 2 修复测试：env_checker 冻结适配（F30/F31）与 worker 命令推导（F35）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from omnicrawler.gui.runner.env_checker import check_omnicrawler
from omnicrawler.gui.runner.worker_task_runner import _derive_worker_command


class _Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_f30_frozen_bundled_survives_timeout(monkeypatch) -> None:
    """冻结模式内置 CLI 存在时，探测超时不判不可用（冷启动慢场景）。"""
    monkeypatch.setattr("omnicrawler.gui.runner.env_checker.resolve_cli_command", lambda p: p)
    monkeypatch.setattr("omnicrawler.core.runtime_paths.bundled_cli_path", lambda: Path("C:/app/omnicrawler.exe"))
    monkeypatch.setattr("omnicrawler.core.runtime_paths.is_frozen", lambda: True)

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="x", timeout=60)

    monkeypatch.setattr("omnicrawler.gui.runner.env_checker.subprocess.run", _raise_timeout)
    available, version = check_omnicrawler("C:/app/omnicrawler.exe")
    assert available is True
    assert "启动较慢" in version


def test_f30_frozen_bundled_survives_nonzero_rc(monkeypatch) -> None:
    monkeypatch.setattr("omnicrawler.gui.runner.env_checker.resolve_cli_command", lambda p: p)
    monkeypatch.setattr("omnicrawler.core.runtime_paths.bundled_cli_path", lambda: Path("C:/app/omnicrawler.exe"))
    monkeypatch.setattr("omnicrawler.core.runtime_paths.is_frozen", lambda: True)
    monkeypatch.setattr(
        "omnicrawler.gui.runner.env_checker.subprocess.run",
        lambda *a, **k: _Result(returncode=1, stdout=""),
    )
    available, _version = check_omnicrawler("C:/app/omnicrawler.exe")
    assert available is True


def test_f31_errors_classified_not_frozen(monkeypatch) -> None:
    """非冻结场景：命令缺失/OSError 返回 False（不吞异常原因）。"""
    monkeypatch.setattr("omnicrawler.gui.runner.env_checker.resolve_cli_command", lambda p: p)
    monkeypatch.setattr("omnicrawler.core.runtime_paths.bundled_cli_path", lambda: None)
    monkeypatch.setattr("omnicrawler.core.runtime_paths.is_frozen", lambda: False)

    monkeypatch.setattr(
        "omnicrawler.gui.runner.env_checker.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no such file")),
    )
    assert check_omnicrawler("omnicrawler") == (False, "")

    monkeypatch.setattr(
        "omnicrawler.gui.runner.env_checker.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert check_omnicrawler("omnicrawler") == (False, "")


def test_f30_non_frozen_timeout_returns_false(monkeypatch) -> None:
    monkeypatch.setattr("omnicrawler.gui.runner.env_checker.resolve_cli_command", lambda p: p)
    monkeypatch.setattr("omnicrawler.core.runtime_paths.bundled_cli_path", lambda: None)
    monkeypatch.setattr("omnicrawler.core.runtime_paths.is_frozen", lambda: False)

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="x", timeout=10)

    monkeypatch.setattr("omnicrawler.gui.runner.env_checker.subprocess.run", _raise_timeout)
    assert check_omnicrawler("omnicrawler") == (False, "")


def test_f35_derive_worker_command(tmp_path: Path) -> None:
    assert _derive_worker_command("omnicrawler") is None
    assert _derive_worker_command("") is None
    assert _derive_worker_command(str(tmp_path / "not-exists.exe")) is None

    cli = tmp_path / "omnicrawler.exe"
    worker = tmp_path / "omnicrawler-worker.exe"
    cli.write_bytes(b"MZ")
    worker.write_bytes(b"MZ")
    assert _derive_worker_command(str(cli)) == [str(worker)]


def test_f35_derive_worker_command_no_worker_binary(tmp_path: Path) -> None:
    cli = tmp_path / "omnicrawler.exe"
    cli.write_bytes(b"MZ")
    assert _derive_worker_command(str(cli)) is None
