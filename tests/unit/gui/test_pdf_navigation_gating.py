"""GUI 层：PDF 工作台按插件可用性条件装配（迁移批次 3 收尾）。

**要钉住的行为**

用户对组件化的原始要求是：

> 组件化的主要目的是为了让用户安心安装，而不是在打开应用之后
> 看到一个空荡荡的 PDF 工作台界面。

所以"装了才显示、没装就不显示"不是顺手实现的效果，而是**核心验收判据**。
这条测试把它固定下来，防止将来有人"简化"成无条件装配。

**判据**

1. 插件可用时，导航里出现 `pdf_workbench` 与 `convert_tool`；
2. 插件不可用时，这两个入口**消失**，且**分组标题不残留**
   （空标题比没有入口更难看）；
3. `central_area._load_pdf_views()` 在不可用时返回 `None`
   （调用方据此跳过视图构造，不摆空壳）；
4. 可用时返回的模块确实带两个视图类。

这些判据都**不启动完整主窗口**——`MainWindow` 需要完整装配环境，
在 CI/无头环境里构造代价高且易受环境影响。测的是"决策正确"，
不是"窗口长得对"，后者属于人工走查范围。
"""

from __future__ import annotations

import pytest

from omnicrawler.gui.navigation import visible_entries


def _keys(capabilities: dict[str, bool]) -> set[str]:
    return {entry.key for entry in visible_entries(capabilities)}


def test_pdf_entries_visible_when_capability_on() -> None:
    """插件可用 ⇒ PDF 工作台与格式互转入口可见。"""
    keys = _keys({"pdf": True})
    assert "pdf_workbench" in keys, "PDF 能力可用，但工作台入口没出现"
    assert "convert_tool" in keys, "PDF 能力可用，但格式互转入口没出现"


def test_pdf_entries_hidden_when_capability_off() -> None:
    """插件不可用 ⇒ 两个入口消失。"""
    keys = _keys({"pdf": False})
    assert "pdf_workbench" not in keys, (
        "PDF 能力不可用，却仍显示工作台入口——"
        "用户点进去会看到空界面，这正是要避免的"
    )
    assert "convert_tool" not in keys, "PDF 能力不可用，却仍显示格式互转入口"


def test_no_empty_group_titles_when_hidden() -> None:
    """入口隐藏后，分组标题不得残留。

    空标题（例如「工具」下面什么都没有）比没有该分组更难看，
    而且会让用户以为功能坏了。
    """
    without = _keys({"pdf": False})
    with_pdf = _keys({"pdf": True})

    # 只在带 pdf 时出现、且是分组标记的键，隐藏后不该留
    only_with_pdf = {key for key in with_pdf if key.startswith("group:")} - {
        key for key in without if key.startswith("group:")
    }
    assert not only_with_pdf, (
        f"隐藏 PDF 入口后仍有空分组残留：{sorted(only_with_pdf)}"
    )


def test_loader_returns_none_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """插件不可用 ⇒ 视图加载器返回 None（调用方跳过构造，不摆空壳）。"""
    from omnicrawler.gui.delegates import central_area
    from omnicrawler.services import document_facade

    monkeypatch.setattr(
        document_facade,
        "document_capability",
        lambda: {"available": False, "reason": "测试用", "missing": [], "plugin_installed": False},
    )
    assert central_area._load_pdf_views() is None, (
        "插件不可用，加载器却返回了视图模块——会在界面上摆一个空壳"
    )


def test_loader_returns_views_when_available() -> None:
    """插件可用 ⇒ 加载器返回的模块带两个视图类。

    插件未安装时跳过：这条测的是"装了之后能拿到什么"，
    而不是"没装时的行为"。
    """
    from omnicrawler.gui.delegates import central_area
    from omnicrawler.services import document_facade

    if not document_facade.document_capability()["available"]:
        pytest.skip("本机未安装 pdf-workbench 插件")

    views = central_area._load_pdf_views()
    assert views is not None
    assert hasattr(views, "PdfWorkbenchView")
    assert hasattr(views, "ConvertView")
