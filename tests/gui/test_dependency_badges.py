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


# ── 镜像加速提示：只在"真实安装失败、轮到镜像源"的时刻触发 ─────────────


class _FakeBox:
    """替换 QMessageBox：记录是否弹过、并模拟用户点了指定按钮。

    ``exec()`` 不真正显示界面（offscreen 下也能跑），``clickedButton()`` 返回
    ``_accept_button``（首个 addButton 的按钮）或 None。由测试构造时用
    ``_chosen_accept`` 指定"用户点的接受按钮还是拒绝按钮"。
    """

    _stack: list[_FakeBox] = []
    _chosen_accept = True

    class Icon:  # 与 QMessageBox.Icon 同名枚举占位（`_show_failure` 会引用）
        Critical = "critical"
        Information = "information"
        Warning = "warning"

    class ButtonRole:
        AcceptRole = "accept"
        RejectRole = "reject"
        ActionRole = "action"

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._accept_button: object | None = None
        self._buttons: list[object] = []
        self.window_title = ""
        _FakeBox._stack.append(self)

    def setWindowTitle(self, title: str) -> None:  # noqa: N802 - Qt 命名
        self.window_title = title

    def setIcon(self, _icon: object) -> None:  # noqa: N802
        pass

    def setText(self, _text: str) -> None:  # noqa: N802
        pass

    def setInformativeText(self, _text: str) -> None:  # noqa: N802
        pass

    def setDetailedText(self, _text: str) -> None:  # noqa: N802
        pass

    def addButton(self, _text: str, _role: object) -> object:  # noqa: N802
        button = object()
        self._buttons.append(button)
        if self._accept_button is None:
            self._accept_button = button
        return button

    def exec(self) -> int:
        return 0

    def clickedButton(self) -> object:  # noqa: N802
        return self._accept_button if _FakeBox._chosen_accept else None


def _failed_official_payload() -> dict:
    return {
        "ok": False,
        "summary": "失败",
        "detail": "pypi.org 连接超时",
        "requirement": "somepkg",
        "attempts": [
            {"host": "pypi.org", "canonical": "pypi.org", "kind": "network", "ok": False}
        ],
    }


def test_mirror_prompt_fires_on_official_network_failure(monkeypatch) -> None:
    """官方源网络失败 ⇒ 弹启用镜像提示；点「启用并重试」⇒ 落盘补丁 + 重试一次。"""
    from omnicrawler.gui.views import dependency_dialog as dd

    retry_sources: list[object] = []
    persisted: list[dict] = []

    def _fake_run(parent, requirement, *, registry, sources_override=None):  # type: ignore[no-untyped-def]
        if sources_override is None:
            return _failed_official_payload()  # 首次：官方源网络失败
        retry_sources.append(list(sources_override))
        return {"ok": True, "summary": "已安装", "detail": "", "requirement": requirement, "attempts": []}

    monkeypatch.setattr(dd, "_run_install_attempt", _fake_run)
    monkeypatch.setattr(dd, "QMessageBox", _FakeBox)
    _FakeBox._stack = []
    _FakeBox._chosen_accept = True  # 用户点「启用并重试」

    config_raw: dict = {}
    ok, enabled = dd.install_with_mirror_offer(
        None, "somepkg", config_raw=config_raw, persist_patch=persisted.append
    )

    assert ok is True
    assert enabled is True
    assert persisted and persisted[0]["mirrors"]["enabled"] is True
    # 同一轮内的镜像重试源列表：官方仍居首位
    assert retry_sources and retry_sources[0][0][1] == "pypi.org"
    # config_raw 就地标记为已启用 ⇒ 同批下一项不再重复弹框
    assert config_raw["mirrors"]["enabled"] is True


def test_mirror_prompt_absent_on_version_failure(monkeypatch) -> None:
    """版本类失败（缺该版本）⇒ 换镜像无用，绝不弹提示。"""
    from omnicrawler.gui.views import dependency_dialog as dd

    def _fake_run(parent, requirement, *, registry, sources_override=None):  # type: ignore[no-untyped-def]
        return {
            "ok": False,
            "summary": "失败",
            "detail": "无满足版本",
            "requirement": requirement,
            "attempts": [
                {"host": "pypi.org", "canonical": "pypi.org", "kind": "version", "ok": False}
            ],
        }

    monkeypatch.setattr(dd, "_run_install_attempt", _fake_run)
    monkeypatch.setattr(dd, "QMessageBox", _FakeBox)
    _FakeBox._stack = []
    _FakeBox._chosen_accept = True

    ok, enabled = dd.install_with_mirror_offer(None, "somepkg", config_raw={})
    assert ok is False
    assert enabled is False
    # 未弹出"官方源连接失败"提示（唯一一次 QMessageBox 是失败原因链）
    titles = [box.window_title for box in _FakeBox._stack]
    assert "官方源连接失败" not in titles
