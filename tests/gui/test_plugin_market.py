from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PyQt6") is None,
    reason="GUI test requires PyQt6",
)


@pytest.fixture(scope="session", autouse=True)
def _qt_app():
    """整个测试会话复用同一个 QApplication，避免反复创建销毁导致
    MotionSignal 等模块级 QObject 单例的 C++ 对象被提前销毁。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    # 不销毁 app：保持单例 QObject（MotionSignal）存活到会话结束


def _make_view(tmp_path):
    """构造一个隔离的插件市场视图（不触发联网）。"""
    from omnicrawl.gui.motion_signal import MotionSignal
    from omnicrawl.gui.views.plugin_market import PluginMarketView

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
    from omnicrawl.gui.views.plugin_market import PluginMarketView

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
    from omnicrawl.gui.views.plugin_market import PluginMarketView

    view = PluginMarketView(project_root=str(tmp_path))

    dest = tmp_path / "plugins_installed"
    (dest / "a").mkdir(parents=True)
    (dest / "a" / "plugin.py.sig").write_bytes(b"\x00" * 8)
    (dest / "b").mkdir(parents=True)
    (dest / "b" / "plugin.py.sig").write_bytes(b"\x00" * 8)
    (dest / "c").mkdir()  # 无 .sig，不算
    ids = set(view._installed_ids())
    assert ids == {"a", "b"}


def test_offline_populate_lists_only_local_installs(tmp_path):
    from omnicrawl.gui.views.plugin_market import PluginMarketView

    view = PluginMarketView(project_root=str(tmp_path))

    # 植入一个本地已安装插件（离线态应被补充展示）
    (tmp_path / "plugins_installed" / "local_only").mkdir(parents=True)
    (tmp_path / "plugins_installed" / "local_only" / "plugin.py.sig").write_bytes(b"\x00" * 8)

    view._state = "offline"
    view._populate_list()
    labels = [view._list.item(i).text() for i in range(view._list.count())]
    assert any("local_only" in lab for lab in labels)


def test_main_window_wires_plugin_market_view(monkeypatch):
    from PyQt6.QtWidgets import QApplication

    from omnicrawl.gui.main import MainWindow
    from omnicrawl.gui.navigation import NavIndex

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    window = MainWindow()

    # 导航索引包含插件市场，且栈中已挂载对应视图
    assert NavIndex.PLUGIN_MARKET == 8
    assert hasattr(window, "_plugin_market")
    assert window._plugin_market is not None

    window.deleteLater()
    QApplication.instance().processEvents()
