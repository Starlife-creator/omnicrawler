from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="GUI test requires PyQt6",
)


@pytest.fixture(scope="session", autouse=True)
def _qt_app():
    """整个测试会话复用同一个 QApplication，避免反复创建销毁导致
    MotionSignal 等模块级 QObject 单例的 C++ 对象被提前销毁。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    # 不销毁 app：保持单例 QObject（MotionSignal）存活到会话结束


def _make_view(tmp_path):
    """构造一个隔离的插件市场视图（不触发联网）。"""
    from omnicrawler.gui.motion_signal import MotionSignal
    from omnicrawler.gui.views.plugin_market import PluginMarketView

    # 确保 MotionSignal 单例绑定到当前存活的 QApplication
    MotionSignal._instance = None
    view = PluginMarketView(project_root=str(tmp_path))
    return view


def test_view_instantiates_offline_by_default(tmp_path):
    view = _make_view(tmp_path)

    # 构造后处于离线态（catalog 未拉取）
    assert view._state == "offline"
    assert view._catalog is None
    # 离线态下列表为空（无 catalog、无本地安装）
    assert view._list.count() == 0
    # 关键按钮在未选择插件时禁用
    view._update_action_buttons()
    assert not view._install_btn.isEnabled()
    assert not view._uninstall_btn.isEnabled()
    assert not view._verify_btn.isEnabled()


def test_is_installed_detects_signature_pair(tmp_path):
    from omnicrawler.gui.views.plugin_market import PluginMarketView

    view = PluginMarketView(project_root=str(tmp_path))

    dest = tmp_path / "plugins_installed" / "demo"
    # 仅 plugin.py、缺 .sig → 不算已安装
    dest.mkdir(parents=True)
    (dest / "plugin.py").write_text("x = 1", encoding="utf-8")
    assert view._is_installed("demo") is False

    # 补充 .sig → 视作已安装
    (dest / "plugin.py.sig").write_bytes(b"\x00" * 8)
    assert view._is_installed("demo") is True

    # 不在 dest_root 下的 id → False
    assert view._is_installed("other") is False


def test_installed_ids_enumerates_signed_plugin_dirs(tmp_path):
    from omnicrawler.gui.views.plugin_market import PluginMarketView

    view = PluginMarketView(project_root=str(tmp_path))

    dest = tmp_path / "plugins_installed"
    (dest / "a").mkdir(parents=True)
    (dest / "a" / "plugin.py.sig").write_bytes(b"\x00" * 8)
    (dest / "b").mkdir(parents=True)
    (dest / "b" / "plugin.py.sig").write_bytes(b"\x00" * 8)
    (dest / "c").mkdir()  # 无 .sig，不算
    ids = set(view._installed_ids())
    assert ids == {"a", "b"}


def test_installed_plugin_verify_fails_closed_with_real_signatures(tmp_path):
    """安装目录的 .sig 用真实 ed25519 演练：未知信任根/篡改 → fail-closed。

    与 GUI 视图 _on_verify 走同一路径（market_client.verify_installed），
    此前测试仅用全零 .sig 验证「存在性」，未覆盖密码学验签（P1-11）。
    """
    from omnicrawler.plugins import signing
    from omnicrawler.plugins.market_client import verify_installed

    private_pem, public_pem = signing.generate_keypair()
    dest_root = tmp_path / "plugins_installed"
    dest = dest_root / "demo"
    dest.mkdir(parents=True)
    plugin = dest / "plugin.py"
    plugin.write_text("PLUGIN_METADATA = {'name': 'demo'}\n", encoding="utf-8")
    (dest / "plugin.py.sig").write_bytes(
        signing.sign_bytes(plugin.read_bytes(), private_pem)
    )
    trust = tmp_path / "trust.pub.pem"
    trust.write_bytes(public_pem)

    ok, reason = verify_installed(dest_root, "demo", str(trust))
    assert ok and reason == "verified"

    # 未知信任根（插件未篡改）→ fail-closed
    _, other_pub = signing.generate_keypair()
    other_trust = tmp_path / "other.pub.pem"
    other_trust.write_bytes(other_pub)
    ok, reason = verify_installed(dest_root, "demo", str(other_trust))
    assert not ok

    # 篡改插件（正确信任根）→ fail-closed
    plugin.write_bytes(plugin.read_bytes() + b"\n# tampered")
    ok, reason = verify_installed(dest_root, "demo", str(trust))
    assert not ok


def test_offline_populate_lists_only_local_installs(tmp_path):
    from omnicrawler.gui.views.plugin_market import PluginMarketView

    view = PluginMarketView(project_root=str(tmp_path))

    # 植入一个本地已安装插件（离线态应被补充展示）
    (tmp_path / "plugins_installed" / "local_only").mkdir(parents=True)
    (tmp_path / "plugins_installed" / "local_only" / "plugin.py.sig").write_bytes(b"\x00" * 8)

    view._state = "offline"
    view._populate_list()
    labels = [view._list.item(i).text() for i in range(view._list.count())]
    assert any("local_only" in lab for lab in labels)


def test_market_entry_capability_helpers_support_legacy_and_new_catalogs():
    from omnicrawler.gui.views.plugin_market import (
        _compatibility,
        _entry_plugin_types,
        _install_block_reason,
        _install_review_text,
        _permission_risk,
    )

    assert _entry_plugin_types({"category": "source"}) == ("source",)
    assert _entry_plugin_types(
        {"plugin_types": ["processor", "exporter", "processor"]}
    ) == ("processor", "exporter")
    assert _permission_risk({"permissions": []})[0] == "low"
    assert _permission_risk({"permissions": ["network:scoped"]})[0] == "medium"
    assert _permission_risk({"execution_mode": "in_process"})[0] == "high"
    assert _compatibility({"compatible_core": ">=0.1.0,<99.0.0"})[0] == "compatible"
    assert _compatibility({"compatible_core": ">99.0.0"})[0] == "incompatible"
    assert _install_block_reason({"plugin_types": ["ui"]})
    review = _install_review_text(
        {
            "name": "Exporter",
            "plugin_types": ["exporter"],
            "permissions": ["network:scoped"],
            "domains": ["api.example.com"],
        }
    )
    assert "导出器" in review
    assert "network:scoped" in review
    assert "api.example.com" in review
    assert "逐项批准" in review


def test_market_filters_by_type_mode_risk_and_search(tmp_path):
    view = _make_view(tmp_path)
    view._state = "ready"
    view._catalog = {
        "plugins": [
            {
                "id": "safe_source",
                "name": "Safe Source",
                "version": "1.0.0",
                "category": "news",
                "plugin_types": ["source"],
                "execution_mode": "subprocess",
                "permissions": [],
                "tags": ["public"],
                "compatible_core": ">=0.1.0",
            },
            {
                "id": "network_exporter",
                "name": "Network Exporter",
                "version": "1.0.0",
                "category": "delivery",
                "plugin_types": ["exporter"],
                "execution_mode": "subprocess",
                "permissions": ["network:scoped"],
                "tags": ["cloud"],
                "compatible_core": ">=0.1.0",
            },
        ]
    }
    view._populate_list()
    assert view._list.count() == 2

    view._type_filter.setCurrentIndex(view._type_filter.findData("exporter"))
    assert view._list.count() == 1
    assert "Network Exporter" in view._list.item(0).text()

    view._risk_filter.setCurrentIndex(view._risk_filter.findData("low"))
    assert view._list.count() == 0
    view._risk_filter.setCurrentIndex(view._risk_filter.findData("medium"))
    assert view._list.count() == 1

    view._type_filter.setCurrentIndex(0)
    view._risk_filter.setCurrentIndex(0)
    view._search_edit.setText("public")
    assert view._list.count() == 1
    assert "Safe Source" in view._list.item(0).text()


def test_market_detail_previews_permissions_and_blocks_incompatible_install(tmp_path):
    view = _make_view(tmp_path)
    view._state = "ready"
    view._catalog = {
        "plugins": [
            {
                "id": "future_exporter",
                "name": "Future Exporter",
                "version": "9.0.0",
                "category": "delivery",
                "plugin_types": ["exporter"],
                "execution_mode": "subprocess",
                "permissions": ["network:scoped"],
                "domains": ["api.example.com"],
                "compatible_core": ">99.0.0",
                "tags": [],
            }
        ]
    }
    view._populate_list()
    view._show_detail("future_exporter")
    detail = view._detail_capabilities.text()
    assert "导出器" in detail
    assert "network:scoped" in detail
    assert "api.example.com" in detail
    assert "不兼容当前版本" in detail
    assert not view._install_btn.isEnabled()
    assert "不兼容" in view._install_btn.toolTip()


def test_main_window_wires_plugin_market_view(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow
    from omnicrawler.gui.navigation import NavIndex

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    window = MainWindow()

    # 导航索引包含插件市场（侧栏行号 10），且栈中已挂载对应视图
    assert NavIndex.PLUGIN_MARKET == 14
    assert hasattr(window, "_plugin_market")
    assert window._plugin_market is not None

    window.deleteLater()
    QApplication.instance().processEvents()


def test_market_install_then_activation_updates_scoped_project_config(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from omnicrawler.gui.main import MainWindow

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    window = MainWindow()
    window._commit_plugin_config_change = lambda: None
    window._plugin_market._installed_ids = lambda: ["legacy", "new_plugin"]

    # 安装完成先建立白名单，并明确排除尚未批准的新插件。
    window._on_market_plugin_installed("new_plugin")
    plugins = window._config.passthrough["plugins"]
    assert plugins["enabled_market_plugins"] == ["legacy"]

    monkeypatch.setattr(
        "omnicrawler.plugins.market_client.verify_installed",
        lambda *_args, **_kwargs: (True, "verified"),
    )
    monkeypatch.setattr(
        "omnicrawler.plugins.plugin_inspector.inspect_plugin",
        lambda _path: SimpleNamespace(
            name="new_plugin",
            version="2.0.0",
            artifact_sha256="a" * 64,
            creator_fingerprint="creator-1",
            permissions=("network:scoped",),
            compatible=True,
            errors=(),
        ),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    window._activate_market_plugin("new_plugin")

    assert plugins["enabled_market_plugins"] == ["legacy", "new_plugin"]
    assert plugins["permission_grants"]["new_plugin"] == {
        "version": "2.0.0",
        "artifact_sha256": "a" * 64,
        "creator_fingerprint": "creator-1",
        "permissions": ["network:scoped"],
    }
    assert "plugins_installed/" in plugins["paths"]

    window.deleteLater()
    QApplication.instance().processEvents()


def test_market_uninstall_removes_enablement_and_grant(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    window = MainWindow()
    window._commit_plugin_config_change = lambda: None
    window._config.passthrough["plugins"] = {
        "enabled_market_plugins": ["demo", "keep"],
        "permission_grants": {"demo": {"permissions": []}, "keep": {"permissions": []}},
    }
    window._on_market_plugin_uninstalled("demo")
    plugins = window._config.passthrough["plugins"]
    assert plugins["enabled_market_plugins"] == ["keep"]
    assert "demo" not in plugins["permission_grants"]

    window.deleteLater()
    QApplication.instance().processEvents()
