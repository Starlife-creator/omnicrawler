"""Q1 试点验收（《优化方案》§11.3）：QML 页面 + 令牌跟随 + 降级 + 打包收集。

前置两条均已实测成立（探测脚本 `.audit-tmp/probe_qml_tokens.py`）：
  ① 令牌 → QML 渲染连通、且**跟随主题**（换主题后强制重绘即变色）；
  ② QML 离屏可渲染、可截图。

★ 离屏截图有**渲染缓存**：改主题后必须先强制重绘（改尺寸）再 `grab()`，
  否则会把"没重绘"误判成"绑定失效"——本文件据此在两次取色之间都改一次尺寸。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from omnicrawler.gui.design_system import ThemeManager
from omnicrawler.gui.views import qml_showcase as showcase_module
from omnicrawler.gui.views.qml_showcase import (
    QmlShowcaseView,
    local_showcase_items,
    qml_available,
    qml_page_source,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _plugin_dir(root: Path, name: str = "demo") -> None:
    """造一个最小本地插件（AST 可读的 PLUGIN_METADATA）。"""
    base = root / "plugins" / name
    base.mkdir(parents=True, exist_ok=True)
    metadata = {"name": name, "version": "1.0.0", "types": ["source"]}
    (base / "plugin.py").write_text(
        f"PLUGIN_METADATA = {metadata!r}\n",
        encoding="utf-8",
    )


def _view(tmp_path: Path, qapp: QApplication) -> tuple[QmlShowcaseView, QWidget]:
    view = QmlShowcaseView(tmp_path)
    holder = QWidget()  # ★ 持有引用：临时容器会被 GC 带走子控件（见 test_charts 的教训）
    view.setParent(holder)
    view.resize(520, 420)
    view.show()
    qapp.processEvents()
    return view, holder


def _grab(qapp: QApplication, view: QmlShowcaseView) -> tuple[int, int]:
    """强制重绘后取 QQuickWidget 的像素（规避离屏 grab 的渲染缓存）。"""
    qapp.processEvents()
    size = view._quick.size()
    view._quick.resize(size.width() + 1, size.height() + 1)
    qapp.processEvents()
    qapp.processEvents()
    image = view._quick.grab().toImage()
    return image.pixelColor(10, 10).name(), image.pixelColor(60, 30).name()


# ── 基本渲染 ─────────────────────────────────────────────


def test_pilot_page_loads_qml(tmp_path: Path, qapp: QApplication) -> None:
    view, _holder = _view(tmp_path, qapp)

    assert qml_available() is True
    assert view._hint.isHidden() is True
    assert view._quick is not None
    assert view._quick.status() == view._quick.Status.Ready
    assert view.showcase_model.rowCount() == 0
    view.shutdown()


def test_pilot_page_lists_local_entries(tmp_path: Path, qapp: QApplication) -> None:
    _plugin_dir(tmp_path)
    view, _holder = _view(tmp_path, qapp)
    view.refresh()

    items = local_showcase_items(tmp_path)
    assert len(items) >= 1
    assert view.showcase_model.rowCount() == len(items)
    assert str(len(items)) in view.accessibleDescription()
    view.shutdown()


def test_pilot_page_has_no_bare_colours_in_qml(tmp_path: Path, qapp: QApplication) -> None:
    """QML 里**不写裸色值**（与 QWidget 侧"禁裸色值"守卫同一条纪律）。"""
    import re

    for qml_file in qml_page_source().parent.glob("*.qml"):
        text = qml_file.read_text(encoding="utf-8")
        # 唯一允许的例外：空态卡用 transparent（不是颜色，是"无背景"）
        offenders = [
            match
            for match in re.findall(r'color:\s*([^\n]+)', text)
            if not match.strip().startswith(("VisualTokens", '"transparent"'))
        ]
        assert offenders == [], f"{qml_file.name} 含裸色值：{offenders}"
    view, _holder = _view(tmp_path, qapp)
    view.shutdown()


def test_pilot_page_degrades_when_qml_missing(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(showcase_module, "qml_available", lambda: False)
    view, _holder = _view(tmp_path, qapp)

    assert view._quick is None
    assert view._hint.isHidden() is False
    assert "PySide6-Addons" in view._hint.text()
    view.shutdown()


def test_qml_available_requires_the_page_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """混合态（运行时可导入、页面文件缺）也必须判不可用。

    2026-09-23 拍板「保留代码、不打包」后，冻结包里**运行时与页面文件都不在**；
    本用例钉的是判据本身：二者缺一即不可用，杜绝"运行时在但 setSource 静默白屏"。
    """
    monkeypatch.setattr(
        showcase_module,
        "qml_page_source",
        lambda: Path("Z:/definitely/missing/ShowcasePage.qml"),
    )
    showcase_module._QML_IMPORT_ERROR = ""
    assert qml_available() is False
    assert "missing" in showcase_module._QML_IMPORT_ERROR


def test_pilot_shutdown_releases_resources(tmp_path: Path, qapp: QApplication) -> None:
    """释放后页面**仍可用**（重建并重连主题信号），且换主题不得抛错。"""
    view, _holder = _view(tmp_path, qapp)
    view.shutdown()

    assert view._bridge is None
    assert view._quick is None

    view.refresh()
    assert view._quick is not None
    assert view._bridge is not None
    manager = ThemeManager.instance()
    manager.apply(qapp, "dark")  # 不得抛 RuntimeError（信号已重连，主题广播正常）
    qapp.processEvents()
    manager.apply(qapp, "light")
    qapp.processEvents()
    view.shutdown()
    assert view._bridge is None


# ── 前置①：令牌跟随主题（钉进测试套件）──────────────────


def test_pilot_tokens_follow_theme(tmp_path: Path, qapp: QApplication) -> None:
    """★ §11.3 前置① 的**可回归**版本：换主题后 QML 渲染必须跟着变。"""
    view, _holder = _view(tmp_path, qapp)
    manager = ThemeManager.instance()
    manager.apply(qapp, "light")
    qapp.processEvents()
    light_canvas, _ = _grab(qapp, view)
    assert light_canvas.casefold() == str(manager.tokens.canvas).casefold()

    manager.apply(qapp, "dark")
    qapp.processEvents()
    dark_canvas, _ = _grab(qapp, view)
    assert dark_canvas.casefold() == str(manager.tokens.canvas).casefold()
    assert dark_canvas != light_canvas, "换主题后 QML 渲染没有跟随（前置①不成立）"

    manager.apply(qapp, "light")
    qapp.processEvents()
    view.shutdown()


def test_pilot_page_source_ships_with_the_package() -> None:
    source = qml_page_source()
    assert source.is_file(), f"QML 页面文件缺失：{source}"
    assert source.name == "ShowcasePage.qml"
