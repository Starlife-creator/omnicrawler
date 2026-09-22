"""安装流程 Mixin 的离屏测试 —— 覆盖 `_on_install` / `_on_install_error` 的全部分支。

由来：P0 切片（失败原因链、更新权限 diff）给 `plugin_market_install.py` 加了新代码路径，
但只有纯逻辑测试，GUI 分支无覆盖 ⇒ 单文件覆盖率下限（30%）判红。
本文件用**最小 stub 宿主**（只满足 Mixin 契约，不建完整视图）补齐分支覆盖。

★ 红线：模态 `QMessageBox` 必须先替换成非阻塞实现，否则离屏跑会挂死而不是失败。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QWidget

from omnicrawler.gui.views import plugin_market_install as install_module
from omnicrawler.gui.views.plugin_market_catalog import MarketCatalogMixin
from omnicrawler.gui.views.plugin_market_install import MarketInstallMixin


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


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


class _StubToastManager:
    _recorder = _ToastRecorder()

    @classmethod
    def instance(cls) -> _ToastRecorder:
        return cls._recorder


class _FakeWorker:
    started = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _FakeWorker.started = True
        self.succeeded = _Sig()
        self.failed = _Sig()
        self.finished = _Sig()

    def start(self) -> None:
        self.started = True

    def deleteLater(self) -> None:
        return None


class _Sig:
    def connect(self, *a: Any, **k: Any) -> None:
        return None


class _StubHost(QWidget, MarketCatalogMixin, MarketInstallMixin):
    """只满足宿主契约的最小宿主（Mixin 单测惯例，不建完整视图）。

    ★ 必须是 QWidget：`_on_install_error` 会以 `parent=self` 弹 QMessageBox，
      非 QWidget 宿主会让 PySide6 的签名匹配直接失败（实测踩过）。
    """

    def __init__(self, *, installed: bool = True, entry_permissions: list[str] | None = None) -> None:
        QWidget.__init__(self)
        self._state = "ready"
        self._catalog = {"_source": "file:///market", "plugins": []}
        self._catalog_url = "file:///market"
        self._dest_root = Path("dest")
        self._trust_source = ""
        self._egress = None
        self._selected_id = "demo"
        self._install_btn = QPushButton()
        self._footer = QLabel()
        self._install_worker = None
        self.refresh_calls: list[str] = []
        self._installed = installed
        self._entry = {
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "permissions": list(entry_permissions or []),
            "execution_mode": "subprocess",
            "required_capabilities": {},
            "maintainer_package_signature_file": "m.sig",
        }

    def _entry_of(self, plugin_id: str) -> dict[str, Any] | None:
        return self._entry

    def _populate_list(self) -> None:
        return None

    def _is_installed(self, plugin_id: str) -> bool:
        return self._installed

    def _update_action_buttons(self, installed: bool | None = None) -> None:
        self.updated_with = installed

    def refresh(self) -> None:  # 覆写真实实现：记录切换后的源，不真拉目录
        self.refresh_calls.append(self._catalog_url)


@pytest.fixture()
def host(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> _StubHost:
    recorder = _ToastRecorder()
    monkeypatch.setattr(
        "omnicrawler.gui.widgets.toast.ToastManager.instance",
        classmethod(lambda cls: recorder),
    )
    shown: list[QMessageBox] = []

    def fake_exec(box: QMessageBox) -> int:
        shown.append(box)
        return int(QMessageBox.StandardButton.No)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    asked: list[str] = []

    def fake_question(*args: Any, **kwargs: Any) -> int:
        # question 是静态方法且内部自建对话框并 exec ⇒ 不替换必挂死（红线）。
        asked.append(str(args[2]) if len(args) > 2 else "")
        return int(QMessageBox.StandardButton.No)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    host = _StubHost()
    host._shown = shown  # type: ignore[attr-defined]
    host._asked = asked  # type: ignore[attr-defined]
    host._toast = recorder  # type: ignore[attr-defined]
    return host


def test_install_error_structured_shows_chain_in_dialog(
    host: _StubHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = {
        "stage": "插件验签",
        "summary": "签名校验失败",
        "advice": "fail-closed：不要绕过",
        "detail": "1. PermissionError: 签名校验失败",
        "chain": [{"type": "PermissionError", "message": "签名校验失败"}],
    }
    host._on_install_error(json.dumps(chain, ensure_ascii=False))
    assert "签名校验失败" in host._footer.text()
    box = host._shown[0]
    assert "原因链" in box.detailedText()
    assert "PermissionError" in box.detailedText()
    assert "不要绕过" in box.text()
    assert host.updated_with is None


def test_install_error_plain_text_falls_back(host: _StubHost) -> None:
    host._on_install_error("第一行\n第二行")
    assert "第一行" in host._footer.text()
    assert "第二行" in host._shown[0].detailedText()


def test_widened_update_requires_confirmation(host: _StubHost, monkeypatch: pytest.MonkeyPatch) -> None:
    """已安装版本无 secrets:read，新版本请求它 ⇒ 扩权必须先经确认，拒绝则不安装。"""
    host._entry["permissions"] = ["files:read", "secrets:read"]
    monkeypatch.setattr(
        install_module,
        "_installed_permissions",
        lambda plugin_dir: ["files:read"],
    )
    host._on_install()
    assert len(host._asked) == 1
    assert "权限变更" in host._asked[0] and "secrets:read" in host._asked[0]
    assert host._install_btn.isEnabled(), "拒绝后按钮必须恢复可用"
    assert host._install_worker is None


def test_low_risk_widened_update_still_asks(host: _StubHost, monkeypatch: pytest.MonkeyPatch) -> None:
    """§4.6 原话：即使低风险，扩权也必须重新呈现并处理授权。"""
    host._entry["permissions"] = ["export:csv"]
    monkeypatch.setattr(install_module, "_installed_permissions", lambda plugin_dir: [])
    host._on_install()
    assert len(host._asked) == 1, "低风险但扩权 ⇒ 仍必须确认"


def test_plain_low_risk_install_starts_worker(host: _StubHost, monkeypatch: pytest.MonkeyPatch) -> None:
    """低风险且无扩权 ⇒ 不弹审查框，直接启动安装 worker。"""
    host._installed = False
    host._entry["permissions"] = ["export:csv"]
    monkeypatch.setattr(install_module, "_InstallWorker", _FakeWorker)
    host._on_install()
    assert _FakeWorker.started
    assert not host._install_btn.isEnabled()


def test_not_ready_state_warns_without_dialog(host: _StubHost) -> None:
    host._state = "offline"
    host._on_install()
    assert "请先联网刷新并选择插件" in host._toast.last("warning")
    assert host._shown == []


def test_local_install_valid_dir_switches_source(
    host: _StubHost, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """选中含 catalog.json 的本地目录 ⇒ 切换源并走既有 refresh（离线安装入口）。"""
    (tmp_path / "catalog.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getExistingDirectory",
        staticmethod(lambda *a, **k: str(tmp_path)),
    )
    host._on_local_install()
    assert host._catalog_url == str(tmp_path)
    assert host.refresh_calls == [str(tmp_path)]


def test_local_install_invalid_dir_warns_without_switching(
    host: _StubHost, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """目录里没有 catalog.json ⇒ 告警且不切换源。"""
    before = host._catalog_url
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getExistingDirectory",
        staticmethod(lambda *a, **k: str(tmp_path)),
    )
    host._on_local_install()
    assert "catalog.json" in host._toast.last("warning")
    assert host._catalog_url == before
    assert host.refresh_calls == []


def test_switch_source_confirms_then_switches(
    host: _StubHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多市场源（§10.2 #4）：https 源经确认后切换并刷新。"""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getText",
        staticmethod(lambda *a, **k: ("https://market.example.com/", True)),
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    host._on_switch_source()
    assert host._catalog_url == "https://market.example.com/"
    assert host.refresh_calls == ["https://market.example.com/"]


def test_switch_source_declined_keeps_source(
    host: _StubHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    before = host._catalog_url
    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getText",
        staticmethod(lambda *a, **k: ("https://market.example.com/", True)),
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    host._on_switch_source()
    assert host._catalog_url == before
    assert host.refresh_calls == []


def test_switch_source_invalid_url_warns(
    host: _StubHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = host._catalog_url
    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getText",
        staticmethod(lambda *a, **k: ("http://insecure.example.com/", True)),
    )
    host._on_switch_source()
    assert "http" in host._toast.last("warning")
    assert host._catalog_url == before
    assert host.refresh_calls == []
