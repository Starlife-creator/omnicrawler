"""依赖徽标 GUI 集成测试（决策四：打开即检测 = 只读徽标，绝不弹框）。

覆盖两条面：
1. 插件市场列表 — 已装插件缺声明依赖时，列表行出现只读 ``[缺依赖 N]`` 徽标；
   依赖齐全时静默（§R2），列表行不含徽标。
2. 环境与依赖面板 — 打开只探测、不自动装、不弹框；缺失项渲染「安装」按钮。
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="GUI test requires PySide6",
)

_MISSING = "omnicrawler_dep_that_does_not_exist_anywhere"


@pytest.fixture(scope="session", autouse=True)
def _qt_app():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _write_plugin(root: Path, plugin_id: str, deps: list[object]) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.py").write_text(
        textwrap.dedent(
            f"""
            PLUGIN_METADATA = {{
                "name": "{plugin_id}",
                "version": "1.0.0",
                "dependencies": {deps!r},
            }}


            def register(registry):
                return None
            """
        ),
        encoding="utf-8",
    )
    return plugin_dir


def test_market_list_shows_dependency_badge_for_missing(tmp_path: Path) -> None:
    """已装插件缺声明依赖时，市场列表行必须出现只读徽标（且不弹框）。"""
    from omnicrawler.gui.motion_signal import MotionSignal
    from omnicrawler.gui.views.plugin_market import PluginMarketView

    installed = tmp_path / "plugins_installed"
    _write_plugin(installed, "demo", [{"name": _MISSING}])

    MotionSignal._instance = None
    view = PluginMarketView(project_root=str(tmp_path))
    view._catalog = {
        "plugins": [
            {
                "id": "demo",
                "name": "Demo",
                "version": "1.0.0",
                "execution_mode": "subprocess",
                "permissions": [],
            }
        ]
    }
    view._state = "ready"

    # 直接注入扫描结果，避免依赖后台线程时序（只测渲染契约）
    from omnicrawler.plugins.plugin_dependency_check import plugin_dependency_status_for_root

    view._dependency_status = plugin_dependency_status_for_root(installed)
    view._populate_list()

    labels = [
        view._list.item(row).text() for row in range(view._list.count())
    ]
    assert any("[缺依赖 1]" in label for label in labels), labels


def test_market_list_silent_when_dependencies_ready(tmp_path: Path) -> None:
    """依赖齐全 ⇒ 列表静默，不出现徽标（§R2 不打扰）。"""
    from omnicrawler.gui.motion_signal import MotionSignal
    from omnicrawler.gui.views.plugin_market import PluginMarketView

    installed = tmp_path / "plugins_installed"
    _write_plugin(installed, "demo", [{"name": "json"}])

    MotionSignal._instance = None
    view = PluginMarketView(project_root=str(tmp_path))
    view._catalog = {
        "plugins": [
            {
                "id": "demo",
                "name": "Demo",
                "version": "1.0.0",
                "execution_mode": "subprocess",
                "permissions": [],
            }
        ]
    }
    view._state = "ready"

    from omnicrawler.plugins.plugin_dependency_check import plugin_dependency_status_for_root

    view._dependency_status = plugin_dependency_status_for_root(installed)
    view._populate_list()

    labels = [view._list.item(row).text() for row in range(view._list.count())]
    assert not any("缺依赖" in label for label in labels), labels


def test_dependency_center_renders_install_buttons(tmp_path: Path) -> None:
    """环境与依赖面板：缺失且可安装的组件渲染「安装」按钮；打开不自动安装。"""
    from PySide6.QtWidgets import QPushButton

    from omnicrawler.gui.motion_signal import MotionSignal
    from omnicrawler.gui.views.dependency_center import (
        CapabilityRow,
        DependencyCenterDialog,
    )

    MotionSignal._instance = None
    dialog = DependencyCenterDialog()
    rows = [
        CapabilityRow(key="a", name="就绪项", tier="standard", ready=True, requirement="a-pkg"),
        CapabilityRow(key="b", name="缺失可装", tier="standard", ready=False, requirement="b-pkg"),
        CapabilityRow(key="c", name="缺失手动", tier="full", ready=False, requirement=""),
    ]
    dialog._render(rows)

    buttons = dialog.findChildren(QPushButton)
    install_buttons = [btn for btn in buttons if btn.text() == "安装"]
    # 只有"缺失且可安装"那行有安装按钮（就绪项、需手动项都没有）
    assert len(install_buttons) == 1
    dialog.close()
