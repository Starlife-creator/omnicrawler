"""Q1 试点验收（《优化方案》§11.3）：QML 页面 + 令牌跟随 + 降级 + 打包收集。

前置两条均已实测成立（探测脚本 `.audit-tmp/probe_qml_tokens.py`）：
  ① 令牌 → QML 渲染连通、且**跟随主题**（换主题后强制重绘即变色）；
  ② QML 离屏可渲染、可截图。

★ 离屏截图有**渲染缓存**：改主题后必须先强制重绘（改尺寸）再 `grab()`，
  否则会把"没重绘"误判成"绑定失效"——本文件据此在两次取色之间都改一次尺寸。
"""

from __future__ import annotations

import re
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


# ── 空态 / 列表互斥（回归守卫）────────────────────────────
#
# 修复前实测到的缺陷（两条都是 QML↔Python 绑定层，不是渲染层）：
#   ① `ShowcaseModel.count` **不存在**——QAbstractListModel 只暴露 `rowCount()`，
#      QML 不把它映射成 `count`；求值为 undefined ⇒ `count === 0` 与 `count > 0`
#      **同时为 false** ⇒ 空态卡和列表**都永不显示**，整页只剩标题+副标题。
#   ② `I18n.emptyHint`（camelCase）与 Python 侧属性名 `empty_hint`（snake_case）
#      不一致 ⇒ undefined ⇒ 日志 `Unable to assign [undefined] to QString`。
# 两者都不产生渲染错误、也不让 `QQuickWidget.status()` 掉出 Ready，所以原有
# 用例（只看 Python 侧 `rowCount()`）全绿也照样漏。

_EMPTY_HINT_OBJECT = "showcaseEmptyHint"
_EMPTY_STATE_OBJECT = "showcaseEmptyState"
_LIST_OBJECT = "showcaseList"

#: 回归注入：把修复后的绑定改回修复前的写法（用于反向断言）
_REGRESSED_HINT_BINDING = ("I18n.empty_hint", "I18n.emptyHint")
_REGRESSED_COUNT_BINDING = ("ShowcaseModel.count", "ShowcaseModel.rowCount")


class _PageProbe:
    """用**真实桥**加载任意 QML 文本，读回绑定解析结果。"""

    def __init__(self, qapp: QApplication) -> None:
        from PySide6.QtQml import QQmlEngine

        from omnicrawler.gui.qml_bridge import QmlTexts, ShowcaseModel, TokenBridge

        self.engine = QQmlEngine()
        self._held: list[object] = []
        self.model = ShowcaseModel()
        self.texts = QmlTexts()
        for key, value in (
            ("ShowcaseModel", self.model),
            ("I18n", self.texts),
            ("VisualTokens", TokenBridge()),
        ):
            self.engine.rootContext().setContextProperty(key, value)
        self._held += [self.model, self.texts]
        qapp.processEvents()

    def state(self, qml_text: str, items: list[dict[str, str]]) -> dict[str, object]:
        from PySide6.QtCore import QObject, QUrl
        from PySide6.QtQml import QQmlComponent

        self.model.set_items(items)
        component = QQmlComponent(self.engine)
        # ★ base URL 必须给真实页面路径：否则同目录的 `ShowcaseCard` 类型解析不到
        #   （`setData` 的 QUrl 为空 ⇒ 无目录上下文 ⇒ "ShowcaseCard is not a type"）。
        base = QUrl.fromLocalFile(str(qml_page_source()))
        component.setData(qml_text.encode("utf-8"), base)
        if component.isError():
            raise AssertionError(f"QML 加载失败：{[e.toString() for e in component.errors()]}")
        root = component.create()
        assert root is not None
        self._held.append(root)
        empty = root.findChild(QObject, _EMPTY_STATE_OBJECT)
        listing = root.findChild(QObject, _LIST_OBJECT)
        hint = root.findChild(QObject, _EMPTY_HINT_OBJECT)
        assert empty is not None and listing is not None and hint is not None, "QML 缺少守卫锚点"
        return {
            "count": self.model.count,
            "empty_visible": bool(empty.property("visible")),
            "list_visible": bool(listing.property("visible")),
            "hint": str(hint.property("text") or ""),
        }


def _page_text() -> str:
    return qml_page_source().read_text(encoding="utf-8")


#: QML 通过 rootContext 注入的三个上下文对象
_CONTEXT_OBJECTS = ("I18n", "ShowcaseModel", "VisualTokens")

_BINDING_REF = re.compile(rf"\b({'|'.join(_CONTEXT_OBJECTS)})\.(\w+)")


def test_showcase_qml_context_bindings_all_resolve(qapp: QApplication) -> None:
    """★ 语义守卫（不依赖 ``objectName`` 锚点）：QML 引用的属性名必须都对得上。

    为什么需要这条：``undefined`` 在 QML 里**不抛错**，只是静默变成 ``false`` /
    空串 —— 属性名写错既不体现在 `QQuickWidget.status()` 上，也不在渲染结果里
    （本例就是空态与列表同时不可见、页面看着"正常"地只剩标题）。所以唯一的判据
    是**名字对得上**，必须逐个解析。
    """
    from omnicrawler.gui.qml_bridge import QmlTexts, ShowcaseModel, TokenBridge

    targets = {"I18n": QmlTexts(), "ShowcaseModel": ShowcaseModel(), "VisualTokens": TokenBridge()}
    refs = sorted(set(_BINDING_REF.findall(_page_text())))

    assert refs, f"页面里没解析到任何上下文属性引用（正则失效？）：{_BINDING_REF.pattern}"
    unresolved = [f"{obj}.{attr}" for obj, attr in refs if not hasattr(targets[obj], attr)]
    assert unresolved == [], f"QML 引用了 Python 侧不存在的属性（QML 求值为 undefined）：{unresolved}"


def test_showcase_empty_state_visible_without_entries(
    tmp_path: Path, qapp: QApplication
) -> None:
    probe = _PageProbe(qapp)
    state = probe.state(_page_text(), [])

    assert state["count"] == 0
    assert state["empty_visible"] is True, "无条目时空态必须显示"
    assert state["list_visible"] is False, "无条目时列表必须隐藏"


def test_showcase_list_visible_with_entries(tmp_path: Path, qapp: QApplication) -> None:
    probe = _PageProbe(qapp)
    items = [{"name": "demo", "version": "1.0.0", "kinds": "source", "summary": "s"}]
    state = probe.state(_page_text(), items)

    assert state["count"] == 1
    assert state["list_visible"] is True, "有条目时列表必须显示"
    assert state["empty_visible"] is False, "有条目时空态必须隐藏"


def test_showcase_empty_hint_binding_resolves(qapp: QApplication) -> None:
    """文案绑定必须解析成非空串（`undefined` 会退化成空串且不报错）。"""
    probe = _PageProbe(qapp)
    state = probe.state(_page_text(), [])

    assert state["hint"].strip(), "I18n.empty_hint 绑定未解析（QML 侧属性名与 Python 侧不一致？）"


@pytest.mark.parametrize(
    ("binding", "value_key", "expected"),
    [
        (_REGRESSED_HINT_BINDING, "hint", ""),
        (_REGRESSED_COUNT_BINDING, "empty_visible", False),
    ],
)
def test_showcase_guard_detects_regressed_bindings(
    qapp: QApplication, binding: tuple[str, str], value_key: str, expected: object
) -> None:
    """★ 反向断言：把绑定改回修复前的写法，守卫**必须变红**。

    守卫只绿不红等于没装护栏（AGENTS.md 二），所以每条正向断言都要配一条
    「装回缺口 ⇒ 判红」的对照。
    """
    current, regressed = binding
    text = _page_text()
    assert current in text, f"待验证的绑定 {current} 已不在页面里，反向断言失效"

    probe = _PageProbe(qapp)
    state = probe.state(text.replace(current, regressed), [])

    assert state[value_key] == expected, (
        f"绑定退回 {regressed} 后 {value_key} 竟未退化，守卫对该缺口不敏感"
    )

