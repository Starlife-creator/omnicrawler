"""U4-代码接线验收：任务日志命中「需要登录」时的联动提示与一键跳转。

纯判据（401 / 302→login 的识别、一次运行只提示一次）在
`test_login_session_logic.py`；这里只验**接线**：
日志回调 → 提示（带"去登录"动作）→ 动作跳到登录页并预填。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from omnicrawler.core.config import AppConfig, load_config
from omnicrawler.gui.delegates import run_controller as run_module
from omnicrawler.gui.delegates.login_session import LoginSessionDelegate
from omnicrawler.gui.delegates.run_controller import RunController
from omnicrawler.gui.navigation import NavIndex
from omnicrawler.gui.views.login_session_logic import LoginHintGate


class _ToastRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def show(self, message: str, **kwargs: Any) -> None:
        self.calls.append({"message": message, **kwargs})

    def last(self) -> dict[str, Any]:
        return self.calls[-1]


@pytest.fixture
def toasts(monkeypatch: pytest.MonkeyPatch) -> _ToastRecorder:
    recorder = _ToastRecorder()

    class _Stub:
        @classmethod
        def instance(cls) -> _ToastRecorder:
            return recorder

    monkeypatch.setattr(run_module, "ToastManager", _Stub)
    return recorder


class _LogConsoleStub:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def append_log(self, message: str, level: str) -> None:
        self.lines.append((message, level))


class _LoginSessionStub:
    def __init__(self) -> None:
        self.pages: list[dict[str, str]] = []

    def open_page(self, *, account: str = "", url: str = "") -> None:
        self.pages.append({"account": account, "url": url})


class _HostStub:
    def __init__(self) -> None:
        self._log_console = _LogConsoleStub()
        self._login_session = _LoginSessionStub()


def _config(tmp_path: Path) -> AppConfig:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: u4, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/list]}\n"
        "session: {persist_cookies: true, name: alice}\n",
        encoding="utf-8",
    )
    return load_config(config_path)


# ── 接线：日志 → 提示 → 动作 ─────────────────────────────


def test_log_line_still_reaches_console_and_announces_login_hint(
    toasts: _ToastRecorder,
) -> None:
    host = _HostStub()
    controller = RunController(host)  # type: ignore[arg-type]

    controller.on_log_line("登录失败: HTTP 401", "error")

    assert host._log_console.lines == [("登录失败: HTTP 401", "error")]
    assert len(toasts.calls) == 1
    assert toasts.last()["kind"] == "warning"
    assert toasts.last()["action_text"] == "去登录"
    assert callable(toasts.last()["action_callback"])


def test_hint_action_jumps_to_login_page(toasts: _ToastRecorder) -> None:
    host = _HostStub()
    controller = RunController(host)  # type: ignore[arg-type]
    controller.on_log_line("HTTP 401 -> https://example.org/signin", "error")

    toasts.last()["action_callback"]()

    assert host._login_session.pages == [{"account": "", "url": "https://example.org/signin"}]


def test_hint_is_announced_only_once_per_run(toasts: _ToastRecorder) -> None:
    host = _HostStub()
    controller = RunController(host)  # type: ignore[arg-type]

    controller.on_log_line("HTTP 401 Unauthorized", "error")
    controller.on_log_line("HTTP 401 Unauthorized", "error")
    controller.on_log_line("HTTP 401 Unauthorized", "error")

    assert len(toasts.calls) == 1


def test_unrelated_log_lines_produce_no_hint(toasts: _ToastRecorder) -> None:
    host = _HostStub()
    controller = RunController(host)  # type: ignore[arg-type]

    controller.on_log_line("已抓取 10 条记录", "info")

    assert toasts.calls == []
    assert len(host._log_console.lines) == 1


def test_hint_gate_is_created_lazily(toasts: _ToastRecorder) -> None:
    controller = RunController(_HostStub())  # type: ignore[arg-type]
    assert controller._login_hint_gate is None

    controller.on_log_line("HTTP 401", "error")

    assert controller._login_hint_gate is not None
    assert controller._login_hint_gate.announced is True


def test_run_task_resets_hint_gate(
    monkeypatch: pytest.MonkeyPatch, toasts: _ToastRecorder
) -> None:
    """★ 新的一次运行要重新开始计数（上次提示过 ≠ 这次不需要登录）。"""
    host = _HostStub()
    host._omnicrawler_available = False
    host._env_checker = SimpleNamespace(check_environment=lambda silent=False: None)
    monkeypatch.setattr(run_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    controller = RunController(host)  # type: ignore[arg-type]
    controller._login_hint_gate = LoginHintGate()
    assert controller._login_hint_gate.should_announce("HTTP 401") is not None

    controller.run_task()

    assert controller._login_hint_gate is not None
    assert controller._login_hint_gate.announced is False


# ── 跳转入口的预填默认值 ─────────────────────────────────


class _NavStub:
    def __init__(self) -> None:
        self.rows: list[int] = []

    def setCurrentRow(self, row: int) -> None:  # noqa: N802 - 对齐 Qt 命名
        self.rows.append(row)


class _ViewStub:
    def __init__(self) -> None:
        self.prefilled: dict[str, str] | None = None

    def prefill(self, *, account: str = "", url: str = "") -> None:
        self.prefilled = {"account": account, "url": url}


def _delegate(tmp_path: Path, config: AppConfig) -> tuple[LoginSessionDelegate, Any]:
    nav = _NavStub()
    host = SimpleNamespace(_config=object(), _nav=nav, _login_app_config=lambda: config)
    delegate = LoginSessionDelegate(host)
    # 让配置缓存命中（等价于 MainWindow 里已构建过一次）
    delegate._cached_source = host._config
    delegate._cached_config = config
    return delegate, nav


def test_page_defaults_come_from_task_config(tmp_path: Path) -> None:
    config = _config(tmp_path)
    delegate, _ = _delegate(tmp_path, config)

    assert delegate._page_defaults() == ("alice", "https://example.org/list")


def test_open_page_prefills_defaults_and_navigates(tmp_path: Path) -> None:
    config = _config(tmp_path)
    delegate, nav = _delegate(tmp_path, config)
    view = _ViewStub()
    delegate.view = view  # type: ignore[assignment]

    delegate.open_page()

    assert view.prefilled == {"account": "alice", "url": "https://example.org/list"}
    assert nav.rows == [NavIndex.LOGIN_SESSION]


def test_open_page_keeps_explicit_values(tmp_path: Path) -> None:
    config = _config(tmp_path)
    delegate, nav = _delegate(tmp_path, config)
    view = _ViewStub()
    delegate.view = view  # type: ignore[assignment]

    delegate.open_page(account="bob", url="https://example.org/signin")

    assert view.prefilled == {"account": "bob", "url": "https://example.org/signin"}
    assert nav.rows == [NavIndex.LOGIN_SESSION]


def test_open_page_without_view_is_a_noop(tmp_path: Path) -> None:
    config = _config(tmp_path)
    delegate, nav = _delegate(tmp_path, config)

    delegate.open_page()

    assert nav.rows == []
