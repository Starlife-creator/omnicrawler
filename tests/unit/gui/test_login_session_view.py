"""U3 会话页**离屏**验收（`QT_QPA_PLATFORM=offscreen` + mock launcher）。

headed 行为本身仍归维护者实机验收（§11.1 U4-验证）；这里覆盖可判定部分：
首次一次性提醒、降级引导、状态/计时刷新、桥接开关的三种结果、会话列表与删除、
以及"配置切换不得丢掉进行中的会话"。

★ 红线：模态 `QMessageBox`（information/question）必须替换，否则离屏会**挂死**
  而不是失败（`test_shortcut_prompt` 记录的既有教训）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

pytest.importorskip("cryptography")

from omnicrawler.core.config import AppConfig, load_config
from omnicrawler.core.secrets_store import SecretsStore
from omnicrawler.fetching.login_session import LoginCapture, LoginPhase
from omnicrawler.fetching.session_state import list_sessions, remove_session, session_name
from omnicrawler.gui.views import login_session as view_module
from omnicrawler.gui.views.login_session import LoginSessionView

_SECRET = "GUI-SESSION-SECRET-VALUE"
_NOTICE_ACK_KEY = "session.notice_ack_v1"


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _FakeKeyring:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self._data.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self._data[(service, account)] = password


class _FakeSettings:
    """最小的 AppSettings 替身（避免测试碰真实 QSettings）。"""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def value(self, key: str, default: Any, value_type: type) -> Any:
        raw = self.data.get(key, default)
        if isinstance(raw, value_type):
            return raw
        try:
            return value_type(raw)
        except (TypeError, ValueError):
            return default

    def set_value(self, key: str, value: object) -> None:
        self.data[key] = value


class _ToastRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def error(self, msg: str) -> None:
        self.calls.append(("error", msg))

    def warning(self, msg: str) -> None:
        self.calls.append(("warning", msg))

    def success(self, msg: str) -> None:
        self.calls.append(("success", msg))

    def info(self, msg: str) -> None:
        self.calls.append(("info", msg))

    def last(self, kind: str) -> str:
        for name, msg in reversed(self.calls):
            if name == kind:
                return msg
        return ""

    def kinds(self) -> list[str]:
        return [name for name, _ in self.calls]


class _AlertStub:
    """模态框替身：记录调用，question 的答案可改。"""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []
        self.answer = QMessageBox.StandardButton.No

    def texts(self, kind: str) -> list[str]:
        return [text for name, text in self.records if name == kind]


class _FakeLauncher:
    """登录窗口替身：完全离线，可注入故障。"""

    def __init__(
        self,
        *,
        domain: str = ".example.org",
        count: int = 2,
        capture_error: BaseException | None = None,
    ) -> None:
        self.domain = domain
        self.count = count
        self.capture_error = capture_error
        self.opened: dict[str, Any] | None = None
        self.capture_calls: list[Path] = []
        self.close_calls = 0
        self.open_window = True

    def open(self, *, url: str, account: str, proxy: str, storage_state_path: Path) -> None:
        self.opened = {
            "url": url,
            "account": account,
            "proxy": proxy,
            "storage_state_path": storage_state_path,
        }
        self.open_window = True

    def capture(self, storage_state_path: Path) -> LoginCapture:
        self.capture_calls.append(storage_state_path)
        if self.capture_error is not None:
            raise self.capture_error
        storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        cookies = [
            {
                "name": f"sid{index}",
                "value": _SECRET,
                "domain": self.domain,
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
            for index in range(self.count)
        ]
        storage_state_path.write_text(json.dumps({"cookies": cookies}), encoding="utf-8")
        return LoginCapture(cookie_count=self.count, domains=(self.domain,))

    def is_open(self) -> bool:
        return self.open_window

    def close(self) -> None:
        self.close_calls += 1
        self.open_window = False


def _config(
    tmp_path: Path, *, persist: bool = True, seeds: str = "[https://example.org/]"
) -> AppConfig:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: u3, workspace: work}\n"
        f"source: {{kind: static_html, seeds: {seeds}}}\n"
        f"session: {{persist_cookies: {str(persist).lower()}, name: default}}\n",
        encoding="utf-8",
    )
    return load_config(config_path)


def _view(
    config: AppConfig,
    *,
    launcher: _FakeLauncher | None = None,
    settings: _FakeSettings | None = None,
) -> LoginSessionView:
    view = LoginSessionView(
        lambda: config,
        settings=settings if settings is not None else _FakeSettings(),
        launcher=launcher if launcher is not None else _FakeLauncher(),
    )
    # 离线：桩掉 DNS 钉扎解析（假域名无 A 记录），策略判定保留
    view.manager._policy.approved_addresses = lambda host, port: (host, port)  # type: ignore[method-assign]
    return view


def _bridge_ready(tmp_path: Path) -> Any:
    """让 CookieSession 的 AES-GCM 落盘在离线环境可用（与 cookie 会话测试同法）。"""
    return patch(
        "omnicrawler.fetching.session.SecretsStore",
        return_value=SecretsStore(tmp_path / "s.bin", keyring_api=_FakeKeyring()),
    )


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> _AlertStub:
    stub = _AlertStub()

    def _information(_parent: Any, _title: str, text: str, *args: Any, **kwargs: Any) -> Any:
        stub.records.append(("information", text))
        return QMessageBox.StandardButton.Ok

    def _question(_parent: Any, _title: str, text: str, *args: Any, **kwargs: Any) -> Any:
        stub.records.append(("question", text))
        return stub.answer

    monkeypatch.setattr(QMessageBox, "information", staticmethod(_information))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    return stub


@pytest.fixture
def toasts(monkeypatch: pytest.MonkeyPatch) -> _ToastRecorder:
    recorder = _ToastRecorder()

    class _Stub:
        @classmethod
        def instance(cls) -> _ToastRecorder:
            return recorder

    monkeypatch.setattr(view_module, "ToastManager", _Stub)
    return recorder


def _start_and_save(view: LoginSessionView, tmp_path: Path, *, url: str = "https://example.org/") -> None:
    """走一遍"打开窗口 → 保存并关闭"（桥接在离线可用的加密存储下完成）。"""
    view._url_edit.setText(url)
    view._on_open_login_window()
    with _bridge_ready(tmp_path):
        view._on_save_and_close()


# ── 一次性提醒（裁定 1）─────────────────────────────────


def test_first_entry_notice_shown_only_once(
    tmp_path: Path, qapp: QApplication, alerts: _AlertStub
) -> None:
    view = _view(_config(tmp_path))
    view.activate()
    view.activate()

    notices = alerts.texts("information")
    assert len(notices) == 1
    assert "0600" in notices[0]  # 保护方式必须讲清楚
    assert str(view._config.workspace) in notices[0]  # 落盘位置
    view.stop_timer()


def test_notice_marker_persisted_in_settings(
    tmp_path: Path, qapp: QApplication, alerts: _AlertStub
) -> None:
    settings = _FakeSettings()
    view = _view(_config(tmp_path), settings=settings)
    view.activate()
    assert settings.data.get(_NOTICE_ACK_KEY) is True
    view.stop_timer()


# ── 降级引导 ─────────────────────────────────────────────


def test_persistence_off_shows_hint_and_disables_open(tmp_path: Path, qapp: QApplication) -> None:
    view = _view(_config(tmp_path, persist=False))
    assert not view._hint_label.isHidden()
    assert "persist_cookies" in view._hint_label.text()
    assert view._open_button.isEnabled() is False
    view.stop_timer()


def test_missing_playwright_shows_hint(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(view_module, "playwright_available", lambda: False)
    view = LoginSessionView(lambda: _config(tmp_path), settings=_FakeSettings())
    assert "Playwright" in view._hint_label.text()
    assert view._open_button.isEnabled() is False
    view.stop_timer()


def test_available_environment_has_no_hint(tmp_path: Path, qapp: QApplication) -> None:
    view = _view(_config(tmp_path))
    assert view._hint_label.isHidden()
    assert view._open_button.isEnabled() is True
    view.stop_timer()


# ── 打开窗口 / 计时（裁定 3）─────────────────────────────


def test_open_window_starts_session(tmp_path: Path, qapp: QApplication) -> None:
    launcher = _FakeLauncher()
    view = _view(_config(tmp_path), launcher=launcher)
    view._url_edit.setText("https://example.org/login")
    view._account_edit.setText("alice")
    view._on_open_login_window()

    assert launcher.opened is not None
    assert launcher.opened["url"] == "https://example.org/login"
    assert launcher.opened["account"] == "alice"
    assert view.manager.phase is LoginPhase.WAITING_LOGIN
    assert "剩余" in view._remaining_label.text()
    assert view._save_button.isEnabled() and view._extend_button.isEnabled()
    view.stop_timer()


def test_open_without_url_warns_and_does_not_launch(
    tmp_path: Path, qapp: QApplication, toasts: _ToastRecorder
) -> None:
    launcher = _FakeLauncher()
    view = _view(_config(tmp_path), launcher=launcher)
    view._on_open_login_window()

    assert launcher.opened is None
    assert toasts.last("warning")
    assert view.manager.phase is LoginPhase.IDLE
    view.stop_timer()


def test_open_rejected_when_persistence_disabled(
    tmp_path: Path, qapp: QApplication, toasts: _ToastRecorder
) -> None:
    launcher = _FakeLauncher()
    view = _view(_config(tmp_path, persist=False), launcher=launcher)
    view._url_edit.setText("https://example.org/")
    view._on_open_login_window()

    assert launcher.opened is None
    assert "persist_cookies" in toasts.last("warning")
    view.stop_timer()


def test_extend_postpones_deadline(tmp_path: Path, qapp: QApplication) -> None:
    view = _view(_config(tmp_path))
    view._url_edit.setText("https://example.org/")
    view._on_open_login_window()
    before = view.manager.snapshot().remaining_seconds

    view._on_extend()

    assert view.manager.snapshot().remaining_seconds >= before
    assert view._extend_button.isEnabled()
    view.stop_timer()


def test_timeout_finalizes_and_saves(tmp_path: Path, qapp: QApplication, toasts: _ToastRecorder) -> None:
    """裁定 3：到点**先保存再关闭**（定时器 tick 走同一条收尾路径）。"""
    launcher = _FakeLauncher()
    view = _view(_config(tmp_path), launcher=launcher)
    view._url_edit.setText("https://example.org/")
    view._on_open_login_window()
    view.manager._deadline = 0.0  # type: ignore[attr-defined] 强制到点

    with _bridge_ready(tmp_path):
        view._on_tick()

    assert view.manager.phase is LoginPhase.SAVED
    assert launcher.capture_calls, "到点必须先保存"
    assert len(list_sessions(view._config.workspace)) == 1
    view.stop_timer()


def test_window_closed_is_detected_by_tick(
    tmp_path: Path, qapp: QApplication, toasts: _ToastRecorder
) -> None:
    launcher = _FakeLauncher()
    view = _view(_config(tmp_path), launcher=launcher)
    view._url_edit.setText("https://example.org/")
    view._on_open_login_window()
    launcher.open_window = False  # 用户直接关掉浏览器窗口

    view._on_tick()

    assert view.manager.phase is LoginPhase.SAVED
    view.stop_timer()


# ── 桥接三态（裁定 1）────────────────────────────────────


def test_save_and_close_bridges_when_enabled(
    tmp_path: Path, qapp: QApplication, toasts: _ToastRecorder
) -> None:
    launcher = _FakeLauncher()
    view = _view(_config(tmp_path), launcher=launcher)
    _start_and_save(view, tmp_path)

    assert view.manager.phase is LoginPhase.SAVED
    assert "success" in toasts.kinds()
    assert "同步" in toasts.last("success")
    assert (view._config.workspace / "sessions" / "default.cookies").is_file()
    view.stop_timer()


def test_bridge_switch_off_keeps_session_but_skips_bridge(
    tmp_path: Path, qapp: QApplication, toasts: _ToastRecorder
) -> None:
    """裁定 1：关闭同步只影响之后的同步，**不删除已存会话**。"""
    launcher = _FakeLauncher()
    view = _view(_config(tmp_path), launcher=launcher)
    view._url_edit.setText("https://example.org/")
    view._on_open_login_window()
    view._bridge_checkbox.setChecked(False)

    view._on_save_and_close()

    assert view.manager.phase is LoginPhase.SAVED
    assert len(list_sessions(view._config.workspace)) == 1  # 会话仍然存下来了
    assert "未同步" in toasts.last("success")
    assert not (view._config.workspace / "sessions" / "default.cookies").is_file()
    view.stop_timer()


def test_bridge_switch_off_is_persisted_and_reloaded(
    tmp_path: Path, qapp: QApplication
) -> None:
    settings = _FakeSettings()
    first = _view(_config(tmp_path), settings=settings)
    first._bridge_checkbox.setChecked(False)
    first.stop_timer()

    second = _view(_config(tmp_path), settings=settings)
    assert second._bridge_checkbox.isChecked() is False
    second.stop_timer()


def test_bridge_failure_reports_session_saved(
    tmp_path: Path, qapp: QApplication, toasts: _ToastRecorder
) -> None:
    """★ 保存成功不能被"同步失败"说成整体失败（否则用户会白重登一次）。"""
    view = _view(_config(tmp_path), launcher=_FakeLauncher(domain=".unrelated.test"))
    _start_and_save(view, tmp_path)

    assert view.manager.phase is LoginPhase.SAVED
    assert "已保存" in toasts.last("warning")
    assert len(list_sessions(view._config.workspace)) == 1
    view.stop_timer()


# ── 会话列表与删除 ───────────────────────────────────────


def test_session_list_renders_rows(tmp_path: Path, qapp: QApplication) -> None:
    view = _view(_config(tmp_path), launcher=_FakeLauncher(count=3))
    _start_and_save(view, tmp_path)

    assert view._table.rowCount() == 1
    assert view._table.item(0, 0).text() == "default"
    assert view._table.item(0, 2).text() == "3"
    assert view._table.isHidden() is False
    assert view._empty.isHidden() is True
    assert view._delete_button.isEnabled() is False  # 未选中
    view.stop_timer()


def test_empty_state_is_shown_without_sessions(tmp_path: Path, qapp: QApplication) -> None:
    view = _view(_config(tmp_path))
    assert view._table.isHidden() is True
    assert view._empty.isHidden() is False
    view.stop_timer()


def test_selecting_a_row_enables_delete(tmp_path: Path, qapp: QApplication) -> None:
    view = _view(_config(tmp_path), launcher=_FakeLauncher())
    _start_and_save(view, tmp_path)

    view._table.setCurrentCell(0, 0)

    assert view._delete_button.isEnabled() is True
    view.stop_timer()


def test_delete_requires_confirmation(
    tmp_path: Path, qapp: QApplication, alerts: _AlertStub, toasts: _ToastRecorder
) -> None:
    view = _view(_config(tmp_path), launcher=_FakeLauncher())
    _start_and_save(view, tmp_path)
    view._table.setCurrentCell(0, 0)

    view._on_delete_selected()  # stub 默认答「否」

    assert len(alerts.texts("question")) == 1
    assert len(list_sessions(view._config.workspace)) == 1
    view.stop_timer()


def test_delete_removes_selected_snapshot(
    tmp_path: Path, qapp: QApplication, alerts: _AlertStub, toasts: _ToastRecorder
) -> None:
    view = _view(_config(tmp_path), launcher=_FakeLauncher())
    _start_and_save(view, tmp_path)
    view._table.setCurrentCell(0, 0)
    alerts.answer = QMessageBox.StandardButton.Yes

    view._on_delete_selected()

    assert list_sessions(view._config.workspace) == ()
    assert "已删除" in toasts.last("success")
    view.stop_timer()


def test_delete_without_selection_warns(
    tmp_path: Path, qapp: QApplication, toasts: _ToastRecorder
) -> None:
    view = _view(_config(tmp_path))
    view._on_delete_selected()
    assert toasts.last("warning")
    view.stop_timer()


def test_remove_session_refuses_paths_outside_sessions_dir(tmp_path: Path) -> None:
    """删除动作的合法范围由代码约束，不由调用方自觉。"""
    config = _config(tmp_path)
    outside = tmp_path / "other.playwright.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError):
        remove_session(outside, workspace=config.workspace)
    assert outside.is_file()


# ── 配置切换与预填 ───────────────────────────────────────


def test_config_switch_is_refused_while_session_active(tmp_path: Path, qapp: QApplication) -> None:
    """★ 进行中的会话不接受新配置：换掉管理器等于没人替窗口收尾。"""
    first = _config(tmp_path)
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = _config(second_dir, seeds="[https://other.test/]")
    current = {"config": first}
    view = LoginSessionView(
        lambda: current["config"], settings=_FakeSettings(), launcher=_FakeLauncher()
    )
    view.manager._policy.approved_addresses = lambda host, port: (host, port)  # type: ignore[method-assign]
    view._url_edit.setText("https://example.org/")
    view._on_open_login_window()
    manager_before = view.manager

    current["config"] = second
    view.refresh()

    assert view.manager is manager_before
    assert view.manager.phase is LoginPhase.WAITING_LOGIN
    view.stop_timer()


def test_config_switch_while_idle_rebuilds_manager(tmp_path: Path, qapp: QApplication) -> None:
    first = _config(tmp_path)
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = _config(second_dir)
    current = {"config": first}
    view = LoginSessionView(
        lambda: current["config"], settings=_FakeSettings(), launcher=_FakeLauncher()
    )
    manager_before = view.manager

    current["config"] = second
    view.refresh()

    assert view.manager is not manager_before
    assert view._config is second
    view.stop_timer()


def test_prefill_sets_login_fields(tmp_path: Path, qapp: QApplication) -> None:
    view = _view(_config(tmp_path))
    view.prefill(account="alice", url="https://example.org/signin")
    assert view._account_edit.text() == "alice"
    assert view._url_edit.text() == "https://example.org/signin"
    view.stop_timer()


def test_stop_timer_is_idempotent(tmp_path: Path, qapp: QApplication) -> None:
    view = _view(_config(tmp_path))
    view._url_edit.setText("https://example.org/")
    view._on_open_login_window()
    view.stop_timer()
    view.stop_timer()
    assert view._timer.isActive() is False


def test_snapshot_name_matches_state_helper(tmp_path: Path, qapp: QApplication) -> None:
    """页面列出的会话名必须就是 session_state 算出来的那个（同一真源）。"""
    view = _view(_config(tmp_path), launcher=_FakeLauncher())
    _start_and_save(view, tmp_path)

    assert list_sessions(view._config.workspace)[0].name == session_name("default|")
    assert view._table.item(0, 0).text() == "default"
    view.stop_timer()


def test_session_row_contains_no_cookie_values(tmp_path: Path, qapp: QApplication) -> None:
    """★ 列表里只能是元数据：cookie 值不得出现在任何单元格。"""
    view = _view(_config(tmp_path), launcher=_FakeLauncher())
    _start_and_save(view, tmp_path)

    cells = [view._table.item(0, column).text() for column in range(view._table.columnCount())]
    assert _SECRET not in " ".join(cells)
    assert cells[2] == "2"
    view.stop_timer()
