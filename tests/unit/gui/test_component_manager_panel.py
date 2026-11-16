"""条目 10：组件管理合并为一个面板，且**只读检测不抢线程**。

背景：组件操作此前散落在两处（「任务工具 → 可选组件」与「环境与依赖」），
用户找不到。本条目把检测/检查/安装/卸载/回滚收进一个可复用控件，
挂在插件市场的「组件」标签页上与「插件」「模板」并列。

**这是合并，不是新建能力**——动作最终都调既有的
``services.component_tools.execute()``。

本文件钉住四条判据，每条都对应一个真实踩过的坑：

1. **构造时不得启动线程** —— 面板可能创建后一直不可见；
   启动线程后对象销毁会撞 ``QThread: Destroyed while thread is still running``
   （实测复现过）。
2. **写操作按钮按真实前置条件禁用** —— 导入要"已检查且兼容"、
   卸载要"有注册表 + 选中行"、回滚要"本会话卸载过"。
   用单一布尔开关会让用户点了才发现条件不满足。
3. **不显示空的可下载列表** —— 组件目录尚未建立时只展示已装 + 本地导入。
4. **面板必须继承 WorkerOwnedWidget** —— 复用仓库既有的非阻塞关闭机制，
   而不是自己写 ``wait()``（那会冻住 GUI 线程）。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.skipif(
    pytest.importorskip("PySide6", reason="组件面板是 Qt 控件") is None,
    reason="需要 PySide6",
)


@pytest.fixture(scope="module")
def qt_app():
    """offscreen QApplication（与同目录其他 GUI 用例同范式）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def panel(qt_app):
    from omnicrawler.gui.views.component_manager import ComponentManagerPanel

    widget = ComponentManagerPanel()
    yield widget
    widget.shutdown()


def test_construction_does_not_start_a_worker(panel) -> None:
    """构造时不得启动线程——否则不可见的面板销毁时会崩。"""
    assert panel._worker is None, (
        "面板构造就启动了后台线程：如果它创建后一直不可见，"
        "对象销毁时会撞 'QThread: Destroyed while thread is still running'"
    )


def test_inherits_worker_owned_widget(panel) -> None:
    """必须复用 WorkerOwnedWidget，而不是自己阻塞等待。"""
    from omnicrawler.gui.core.worker_owned_widget import WorkerOwnedWidget

    assert isinstance(panel, WorkerOwnedWidget), (
        "未继承 WorkerOwnedWidget：关闭时会阻塞 GUI 线程或泄漏线程"
    )


def test_import_disabled_until_compatible_preview(panel) -> None:
    """未检查/不兼容时，安装按钮必须是禁用的。"""
    assert panel._import_btn.isEnabled() is False

    # 模拟一次通过的检查结果
    panel._apply("inspect", {
        "compatible": True,
        "package_sha256": "a" * 64,
        "registry_sha256": "b" * 64,
        "component": {"name": "ocr-demo", "version": "1.0.0"},
    })
    assert panel._import_btn.isEnabled() is True


def test_uninstall_requires_selection(panel) -> None:
    """有注册表但没选中行时，卸载按钮仍应禁用。"""
    panel._apply("list", {
        "registry_sha256": "c" * 64,
        "components": [{"name": "ocr-demo", "version": "1.0.0"}],
    })
    assert panel._list.count() == 1
    assert panel._uninstall_btn.isEnabled() is False, "未选中行却允许卸载"

    panel._list.setCurrentRow(0)
    assert panel._uninstall_btn.isEnabled() is True


def test_rollback_only_after_uninstall_in_session(panel) -> None:
    """回滚只在"本会话卸载过"之后可用。"""
    assert panel._rollback_btn.isEnabled() is False
    panel._apply("uninstall", {
        "component": {"name": "ocr-demo"},
        "registry_sha256": "d" * 64,
    })
    # 模拟卸载后注册表变空
    panel._apply("list", {"registry_sha256": "d" * 64, "components": []})
    assert panel._rollback_btn.isEnabled() is True


def test_no_fake_download_list_when_nothing_installed(panel) -> None:
    """未安装任何组件时：列表为空 + 提示语，不画空的可下载列表。"""
    panel._apply("list", {"registry_sha256": "e" * 64, "components": []})
    assert panel._list.count() == 0
    text = panel._status.text()
    assert "尚未安装" in text or "未安装" in text, f"提示语不可读：{text!r}"


def test_component_description_includes_uninstall_impact() -> None:
    """组件描述必须包含卸载影响——用户点卸载前要知道会发生什么。"""
    from omnicrawler.gui.views.component_manager import describe

    text = describe({
        "name": "ocr-demo", "version": "1.0.0", "purpose": "离线 OCR",
        "disk_bytes": 1024, "uninstall_impact": "依赖此组件的任务将无法运行",
    })
    assert "ocr-demo" in text
    assert "卸载影响" in text
    assert "依赖此组件的任务将无法运行" in text


def test_inspect_failure_is_surfaced_not_swallowed(panel) -> None:
    """检查失败必须显示可读原因，不能静默。"""
    panel._on_failed("组件包没有可用的受信签名密钥")
    text = panel._status.text()
    assert "失败" in text
    assert "受信签名" in text
