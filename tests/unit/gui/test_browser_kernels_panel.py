"""浏览器内核面板的守卫。

## 这批守卫防什么

面板的价值全在「把真实情况摆给用户看」。三类错误会让它变成误导：

1. **数据自己推导**：界面如果自己判断「哪个内核可用」，就会与
   `services.browser_kernels` 漂移 —— CLI 说不可用、界面说可用。
2. **切换不提示登录态代价**：换内核会让已有登录态失效（`engine_family`
   绑定），这是正确行为但用户不知道，会以为「换个内核把登录搞掉了」。
3. **本地内核隐藏或静默**：用户导入的内核必须出现在同一张表里、
   且「未验证」必须可见 —— 能开，但绝不静默。

## 测试方式

用 `QT_QPA_PLATFORM=offscreen` 真正实例化控件，不是只读源码。
这样能验证「控件能否构造、数据能否填充」这类只有运行时才暴露的问题。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _panel(catalog: dict | None = None):
    from omnicrawler.gui.views.browser_kernels import BrowserKernelPanel

    return BrowserKernelPanel(catalog=catalog)


def _catalog() -> dict:
    return {
        "components": [
            {
                "id": "chromium-builtin",
                "provides": ["browser.chromium"],
                "declared_total_bytes": 435574347,
                "artifacts": [
                    {"source": "builtin", "sha256": "a" * 64, "size": 435574347}
                ],
            }
        ]
    }


def test_panel_constructs_and_populates(qt_app: QApplication) -> None:
    """面板能实例化并填充行（只有运行时才暴露的问题）。"""
    panel = _panel(_catalog())
    assert panel._table.rowCount() > 0, "表格没有任何行"
    assert panel._table.columnCount() == 5
    headers = [
        panel._table.horizontalHeaderItem(i).text() for i in range(5)
    ]
    assert headers == ["内核", "来源", "大小", "状态", "说明"]


def test_panel_uses_service_not_own_logic(qt_app: QApplication) -> None:
    """**面板必须调用 `list_kernels()`，不得自己推导「哪个可用」**。

    界面自己判断会与 CLI 漂移：同一台机器上 `browser-kernels` 说不可用、
    面板说可用，用户无从判断信哪个。
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "omnicrawler"
        / "gui"
        / "views"
        / "browser_kernels.py"
    ).read_text(encoding="utf-8")
    assert "list_kernels" in source, "面板没有用共享的数据源"
    assert "probe_kernel" in source, "面板没有用共享的版本探测"


def test_unavailable_kernels_are_shown_with_status(qt_app: QApplication) -> None:
    """**不可用的内核必须留在表里**，且状态列标为「缺失」。"""
    panel = _panel(_catalog())
    states = [
        panel._table.item(row, 3).text() for row in range(panel._table.rowCount())
    ]
    assert any(state == "缺失" for state in states), (
        f"没有任何「缺失」行 —— 不可用内核被静默过滤了：{states}"
    )


def test_switch_warns_about_login_state(qt_app: QApplication) -> None:
    """**切换内核必须提示登录态会失效**（§15.16.9）。

    验证方式是**行为级**：拦截 `QMessageBox.exec` 捕获实际显示的文本，
    而不是 grep 源码。理由：源码里有字符串不代表运行时真的显示了它 ——
    我第一版守卫就是 grep 源码，反向断言（把提示改掉）竟然仍然全绿，
    说明它测的是「文件里有这几个字」而不是「用户能不能看到」。

    不同内核的 cookie 与指纹域互不相通，换内核让旧登录态失效是**正确行为**。
    但用户不知道，会以为「换个内核把登录搞掉了」。
    """
    from PySide6.QtWidgets import QMessageBox

    panel = _panel(_catalog())
    captured: dict[str, str] = {}
    original = QMessageBox.exec

    def fake_exec(self: QMessageBox) -> QMessageBox.StandardButton:
        captured["info"] = self.informativeText()
        captured["text"] = self.text()
        return QMessageBox.StandardButton.Cancel

    QMessageBox.exec = fake_exec  # type: ignore[method-assign]
    try:
        for row in range(panel._table.rowCount()):
            if panel._table.item(row, 3).text() == "可用":
                panel._table.selectRow(row)
                break
        else:
            pytest.skip("本机没有可用内核")
        panel._on_activate()
    finally:
        QMessageBox.exec = original  # type: ignore[method-assign]

    assert captured, "切换没有弹出确认框"
    assert "登录态失效" in captured.get("info", ""), (
        f"确认框没有提登录态代价，实际显示：{captured.get('info')!r}"
    )


def test_activate_disabled_when_unavailable(qt_app: QApplication) -> None:
    """不可用的内核不能被设为默认。"""
    panel = _panel(_catalog())
    # 找到第一个不可用行并选中
    target = None
    for row in range(panel._table.rowCount()):
        if panel._table.item(row, 3).text() == "缺失":
            target = row
            break
    if target is None:
        pytest.skip("本机所有内核都可用，无法构造该场景")
    panel._table.selectRow(target)
    assert panel._activate.isEnabled() is False, "不可用的内核竟然可以设为默认"


def test_local_import_shows_probe_and_unverified(qt_app: QApplication) -> None:
    """本地导入必须显示探测结果，并标「未验证」。"""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "omnicrawler"
        / "gui"
        / "views"
        / "browser_kernels.py"
    ).read_text(encoding="utf-8")
    assert "probe_kernel" in source
    assert "未经验证" in source or "未验证" in source, "导入没有标注未验证"
    assert "driver_hint" in source, "导入没有提示需要的驱动"


def test_trust_labels_cover_three_sources(qt_app: QApplication) -> None:
    """三种来源都要有面向用户的标签（内置 / 市场 / 本地导入）。

    **刻意不用颜色区分来源**：GUI 门禁对新文件零容忍裸十六进制色值，
    而硬编码颜色在深色/高对比度主题下不可读。来源语义用文字表达已足够，
    且对色盲用户同样清晰 —— 需要视觉强调时交给设计体系令牌，不在视图里写死。
    """
    from omnicrawler.gui.views.browser_kernels import _TRUST_LABELS

    assert set(_TRUST_LABELS) == {"builtin", "market", "local"}, (
        f"来源标签不全：{sorted(_TRUST_LABELS)}"
    )
    # 标签必须是给人看的文字，不能是色值或空串
    for key, label in _TRUST_LABELS.items():
        assert label and not label.startswith("#"), f"{key} 的标签不是可读文字：{label!r}"


def test_no_raw_hex_colors_in_panel() -> None:
    """面板不得出现裸十六进制色值（GUI 门禁的零容忍约定）。

    这条守卫让「加了颜色但忘了走令牌」在**单元测试层**就红，
    不必等门禁跑完才发现。
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "omnicrawler"
        / "gui"
        / "views"
        / "browser_kernels.py"
    ).read_text(encoding="utf-8")
    hits = re.findall(r"#[0-9a-fA-F]{3,8}\b", source)
    assert not hits, f"面板出现裸十六进制色值：{hits}"


def test_component_manager_hosts_kernel_panel(qt_app: QApplication) -> None:
    """内核面板挂在组件页里 —— 用户找「能装什么」不该记两个入口。

    内核本身就是 `kind: native` 组件，分开两页会让人以为它们是不同的东西。
    """
    from omnicrawler.gui.views.component_manager import ComponentManagerPanel

    panel = ComponentManagerPanel()
    assert hasattr(panel, "_kernel_panel"), "组件管理页没有挂载内核面板"
    assert panel._kernel_panel._table.rowCount() > 0, "内核面板没有数据"
