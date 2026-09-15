"""字段表的新列「属性 / 取值方式」：模型行为 + 落盘往返（W2.2 收官）。

## 为什么要有这一组

加这两列之前，任务画布上**根本没法指定属性**（``attribute`` 只从页面分析/视觉点选被动接收），
于是"取本条记录的 href"这类规则在表单里建不出来（引擎支持、GUI 表达不了）。
这组测试锁三件事：

1. **列与表头**：两列确实存在（位置契约驱动的标签）；
2. **编辑落到模型**：属性 → ``FieldDef.attribute``、取值方式 → ``FieldDef.position``，
   且非法位置值被拒绝（不会静默变成别的语义）；
3. **落盘形状**：配置里出现 ``attr``、**不出现** ``position``（位置由形状推导，零迁移），
   重新加载后位置仍能显式还原。
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="GUI 测试需要 PySide6",
)

# QApplication 必须保持模块级引用：局部创建会被 GC 连带销毁 Qt 控件树
_APP = None


def _ensure_app() -> None:
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication

        _APP = QApplication.instance() or QApplication([])


def _model():
    _ensure_app()
    from omnicrawler.gui.views.task_canvas_components import FieldTableModel

    model = FieldTableModel()
    from omnicrawler.gui.core.config_model import FieldDef

    model.append(FieldDef(name="链接", selector="a.item", selector_type="css"))
    return model


def test_headers_include_attribute_and_position_columns() -> None:
    model = _model()
    headers = [
        model.headerData(column, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Orientation.Horizontal)
        for column in range(model.columnCount())
    ]
    assert model.columnCount() == 5
    assert headers == ["名称", "选择器", "类型", "属性", "取值方式"], headers


def test_editing_attribute_and_position_updates_the_field() -> None:
    from PySide6.QtCore import Qt

    from omnicrawler.core.field_value_source import POSITION_ELEMENT_ATTR
    from omnicrawler.gui.views.task_canvas_components import FieldTableModel

    model = _model()
    assert model.setData(model.index(0, FieldTableModel.COLUMN_ATTRIBUTE), "href")
    assert model.setData(model.index(0, FieldTableModel.COLUMN_POSITION), POSITION_ELEMENT_ATTR)

    field = model.rows()[0]
    assert field.attribute == "href"
    assert field.position == POSITION_ELEMENT_ATTR
    assert field.validate() == [], "选「元素自身属性」并给了属性名 ⇒ 合法（选择器可留空）"

    # 非法位置值必须被拒绝，而不是静默落到别的位置
    assert model.setData(model.index(0, FieldTableModel.COLUMN_POSITION), "self") is False
    assert model.rows()[0].position == POSITION_ELEMENT_ATTR

    # 中文标签同样被接受（委托给的是下拉项文本）
    assert model.setData(model.index(0, FieldTableModel.COLUMN_POSITION), "子元素")
    assert model.rows()[0].position == "child"
    # 撤销"元素自身属性"后仍保留属性名（切回来即可用），但位置已变
    assert model.rows()[0].attribute == "href"
    assert model.data(model.index(0, FieldTableModel.COLUMN_POSITION), Qt.ItemDataRole.EditRole) == "child"


def test_display_is_label_and_edit_role_is_key() -> None:
    from PySide6.QtCore import Qt

    from omnicrawler.gui.views.task_canvas_components import FieldTableModel

    model = _model()
    index = model.index(0, FieldTableModel.COLUMN_POSITION)
    # 默认（选择器非空）＝ 子元素
    assert model.data(index, Qt.ItemDataRole.EditRole) == "child"
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "子元素"


def test_selector_cell_locks_when_value_comes_from_the_element_itself() -> None:
    """选了「取元素自身」两种方式后，选择器不参与取值 ⇒ 那一格不应可编辑。"""
    from PySide6.QtCore import Qt

    from omnicrawler.core.field_value_source import POSITION_ELEMENT, POSITION_ELEMENT_ATTR
    from omnicrawler.gui.views.task_canvas_components import FieldTableModel

    model = _model()
    selector_cell = model.index(0, FieldTableModel.COLUMN_SELECTOR)
    assert model.flags(selector_cell) & Qt.ItemFlag.ItemIsEditable

    for position in (POSITION_ELEMENT, POSITION_ELEMENT_ATTR):
        assert model.setData(model.index(0, FieldTableModel.COLUMN_POSITION), position)
        assert not (model.flags(selector_cell) & Qt.ItemFlag.ItemIsEditable), position

    assert model.setData(model.index(0, FieldTableModel.COLUMN_POSITION), "child")
    assert model.flags(selector_cell) & Qt.ItemFlag.ItemIsEditable


def test_saved_yaml_uses_attr_key_and_omits_position() -> None:
    """落盘形状：``attr`` 出现、``position`` **不出现**；重新加载后位置可显式还原。"""
    import ruamel.yaml

    from omnicrawler.core.field_value_source import POSITION_ELEMENT_ATTR
    from omnicrawler.gui.core.config_serializer import from_yaml, to_yaml

    config = from_yaml(
        """project: {name: t, workspace: work/t}
source: {kind: static_html, seeds: [https://example.org/]}
extract:
  mode: html
  item_selector: a.item
  fields: {}
"""
    )
    from omnicrawler.gui.core.config_model import FieldDef

    config.fields = [
        FieldDef(name="链接", selector="", attribute="href", position=POSITION_ELEMENT_ATTR)
    ]
    saved = to_yaml(config)
    payload = ruamel.yaml.YAML(typ="safe").load(saved)
    field = payload["extract"]["fields"]["链接"]
    assert field.get("attr") == "href", field
    # 空选择器**有意省略**（不往合法规则里注入无意义的空键），引擎读缺失键即空串
    assert not field.get("selector"), field
    assert "position" not in field, "位置由形状推导，不应写进配置（零迁移）"

    reloaded = from_yaml(saved)
    assert reloaded.fields[0].position == POSITION_ELEMENT_ATTR
    # 往返后校验必须干净（选择器为空是合法的——位置说明它就是"取元素自身属性"）
    assert reloaded.fields[0].validate() == []
