"""GUI delegates 布线契约冒烟（FINAL 长期债 #4）。

此前 8 个委托类基本无直接测试（审查报告 T-1）。本文件先钉住**布线契约**：
每个委托在 MainWindow 上有实例、_mw 回指正确、已知未接线的类被显式记录。
逐委托行为级测试随解耦重构（长期债 #1 分阶段）补充。
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="delegate contract test requires PySide6",
)

from omnicrawler.gui.delegates.config_manager import ConfigManager  # noqa: E402
from omnicrawler.gui.delegates.env_checker import EnvironmentChecker  # noqa: E402
from omnicrawler.gui.delegates.error_dialog import ErrorDialogHelper  # noqa: E402
from omnicrawler.gui.delegates.help_dialog import HelpDialogManager  # noqa: E402
from omnicrawler.gui.delegates.menu import MenuBuilder  # noqa: E402
from omnicrawler.gui.delegates.theme import ThemeManager  # noqa: E402
from omnicrawler.gui.delegates.toolbar import ToolbarManager  # noqa: E402

# 由 MainWindow 构造并持有、且 _mw 回指 MainWindow 的委托
MW_BACKED_DELEGATES = (
    MenuBuilder,
    ToolbarManager,
    ThemeManager,
    ErrorDialogHelper,
    EnvironmentChecker,
    HelpDialogManager,
    ConfigManager,  # main.py 以别名 ConfigDelegate 接线（self._config_delegate）
)


@pytest.fixture
def main_window(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    yield window
    window.deleteLater()
    app.processEvents()


def _instance_types(mw) -> set[type]:  # type: ignore[no-untyped-def]
    return {type(value) for value in vars(mw).values()}


def test_mw_backed_delegates_are_wired(main_window) -> None:  # type: ignore[no-untyped-def]
    types_in_mw = _instance_types(main_window)
    for cls in MW_BACKED_DELEGATES:
        assert cls in types_in_mw, f"委托 {cls.__name__} 未在 MainWindow 上接线"


def test_delegate_backref_points_to_main_window(main_window) -> None:  # type: ignore[no-untyped-def]
    for attr in ("_menu_builder", "_toolbar_manager", "_theme_manager",
                 "_error_helper", "_env_checker", "_help_dialogs",
                 "_config_delegate"):
        delegate = getattr(main_window, attr)
        assert delegate._mw is main_window, f"{attr}._mw 未回指主窗口"


def test_run_controller_binds_when_project_loaded(main_window, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """RunController 为懒构建：绑定项目（_bind_application_controllers）后出现。

    注意命名重叠：`_run_controller` 持有的是 services.controllers.RunController
    （应用服务层控制器）；gui delegates 的 RunDelegate 另接在 _run_delegate。
    """
    from omnicrawler.services.controllers import RunController as ServiceRunController

    # 未加载项目前为 None
    assert main_window._run_controller is None

    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: t, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    main_window._config_path = str(config_path)
    main_window._bind_application_controllers()

    assert isinstance(main_window._run_controller, ServiceRunController)
    # GUI 委托层的 RunDelegate 以 MainWindow 为宿主，回指主窗口
    assert main_window._run_delegate._mw is main_window
