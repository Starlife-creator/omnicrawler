from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PyQt6") is None,
    reason="GUI test requires PyQt6",
)


@pytest.fixture(scope="session", autouse=True)
def _qt_app():
    """整个测试会话复用同一个 QApplication（同 test_plugin_market 约定）。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _make_view(tmp_path):
    from omnicrawl.gui.motion_signal import MotionSignal
    from omnicrawl.gui.views.template_market import TemplateMarketView

    MotionSignal._instance = None
    return TemplateMarketView(
        catalog_url="",
        project_root=str(tmp_path),
        trust_source=str(tmp_path / "configs" / "plugin_trust.pub.pem"),
    )


def test_view_instantiates_offline_by_default(tmp_path) -> None:
    view = _make_view(tmp_path)
    assert view._state == "offline"
    assert view._catalog is None
    assert view._list.count() == 0
    assert view._dest_root.name == "templates_installed"
    view._update_action_buttons()
    assert not view._install_btn.isEnabled()
    assert not view._uninstall_btn.isEnabled()
    assert not view._verify_btn.isEnabled()


def test_populate_lists_template_entries(tmp_path) -> None:
    view = _make_view(tmp_path)
    view._catalog = {
        "templates": [
            {"id": "generic/demo", "name": "演示模板", "version": "1.0.0"},
            {"id": "social/demo", "name": "社交模板", "version": "2.0.0"},
        ]
    }
    view._state = "ready"
    view._populate_list()
    assert view._list.count() == 2
    view._list.setCurrentRow(0)
    assert view._selected_id == "generic/demo"


def test_market_view_embeds_template_tab(tmp_path) -> None:
    from omnicrawl.gui.motion_signal import MotionSignal
    from omnicrawl.gui.views.plugin_market import PluginMarketView
    from omnicrawl.gui.views.template_market import TemplateMarketView

    MotionSignal._instance = None
    view = PluginMarketView(project_root=str(tmp_path))
    assert view._tabs.count() == 2
    assert isinstance(view._template_market, TemplateMarketView)
