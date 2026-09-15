"""Reusable worker, model and UI primitives for :mod:`task_canvas`."""

from __future__ import annotations

from typing import Any, Literal, cast

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ...core.field_value_source import COMMON_ATTRIBUTES, POSITION_BY_KEY, POSITION_CHILD
from ..core.config_model import FieldDef
from ..design_system import SPACING, repolish_widget
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip


class PlanReviewWorker(QThread):
    """Build an optional AI-assisted plan without coupling it to the canvas UI."""

    result_ready = Signal(object)
    ai_unavailable = Signal(str)
    ai_error = Signal(str)

    def __init__(
        self,
        request: str,
        parent: QWidget | None = None,
        project_root: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._project_root = project_root

    def run(self) -> None:
        try:
            from ...core.ai_env import load_ai_privacy

            privacy = load_ai_privacy(self._project_root)
            if not privacy.get("allow_page_text", True):
                self.ai_unavailable.emit(_("AI 页面文本外发已按隐私设置禁用，已使用本地解析"))
                return

            from ...services.natural_language_task import compile_with_ai

            provider = self._load_provider()
            if provider is None:
                self.ai_unavailable.emit(_("AI 未启用：请在「AI 服务中心」配置后重试"))
                return

            self.result_ready.emit(compile_with_ai(self._request, provider))
        except Exception as exc:  # noqa: BLE001 - errors must be surfaced in the UI
            self.ai_error.emit(str(exc).strip() or type(exc).__name__)
        finally:
            self.deleteLater()

    def _load_provider(self) -> object | None:
        from ...services.ai_providers import provider_from_env

        return cast("object | None", provider_from_env(project_root=self._project_root))


class FieldTableModel(QAbstractTableModel):
    """Editable, progressively disclosed field model for the task canvas.

    列（2026-09-15 追加后两列，前三个索引保持不变以免破坏既有用法）：
    ``名称 | 选择器 | 类型 | 属性 | 取值方式``。
    后两列来自「取值位置」契约（``core/field_value_source.py``）——加它们之前，画布上
    **根本没法指定属性**（``attribute`` 只从页面分析/视觉点选被动接收），
    于是"取本条记录的 href"这类规则在任务表单里建不出来。
    """

    _HEADERS: tuple[str, ...] = (_("名称"), _("选择器"), _("类型"), _("属性"), _("取值方式"))
    _INITIAL_VISIBLE_ROWS = 10

    #: 取值方式列的列号（供委托与测试引用，避免散落的魔法数字）
    COLUMN_NAME = 0
    COLUMN_SELECTOR = 1
    COLUMN_TYPE = 2
    COLUMN_ATTRIBUTE = 3
    COLUMN_POSITION = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fields: list[FieldDef] = []
        self._visible: int | None = self._INITIAL_VISIBLE_ROWS

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        if parent is not None and parent.isValid():
            return 0
        total = len(self._fields)
        return total if self._visible is None else min(total, self._visible)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        return len(self._HEADERS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._fields):
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        field = self._fields[index.row()]
        column = index.column()
        if column == self.COLUMN_ATTRIBUTE:
            return field.attribute or ""
        if column == self.COLUMN_POSITION:
            key = field.resolved_position()
            # 显示给人看的是中文标签；`EditRole` 给委托用**键**，才能正确预选下拉项
            if role == Qt.ItemDataRole.DisplayRole:
                return position_label(key)
            return key
        return (field.name, field.selector, field.selector_type)[column]

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section] if section < len(self._HEADERS) else None
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # type: ignore[override]
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        # 「取元素自身」时选择器不参与取值 ⇒ 那一格不可编辑（值仍保留，切回来即可用）
        if index.column() == self.COLUMN_SELECTOR:
            row = index.row()
            if 0 <= row < len(self._fields) and self._fields[row].resolved_position() != POSITION_CHILD:
                return base
        return base | Qt.ItemFlag.ItemIsEditable

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        field = self._fields[index.row()]
        text = str(value)
        column = index.column()
        if column == self.COLUMN_NAME:
            if not text.strip():
                return False
            field.name = text.strip()
        elif column == self.COLUMN_SELECTOR:
            field.selector = text
        elif column == self.COLUMN_TYPE:
            field.selector_type = (
                cast(Literal["css", "xpath", "jsonpath"], text)
                if text in ("css", "xpath", "jsonpath")
                else "css"
            )
        elif column == self.COLUMN_ATTRIBUTE:
            field.attribute = text.strip() or None
        elif column == self.COLUMN_POSITION:
            position = position_key(text) if text else None
            if position is None and text:
                return False
            field.position = position
            # 位置变了 ⇒ 选择器那一格的可编辑性也变了：整行发一次变更，让视图重查 flags
            first = self.index(index.row(), 0)
            last = self.index(index.row(), len(self._HEADERS) - 1)
            self.dataChanged.emit(
                first, last, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole]
            )
            return True
        else:
            return False
        self.dataChanged.emit(
            index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole],
        )
        return True

    def set_fields(self, fields: list[FieldDef]) -> None:
        self._fields = list(fields)
        self._visible = self._INITIAL_VISIBLE_ROWS
        self.layoutChanged.emit()

    def rows(self) -> list[FieldDef]:
        return list(self._fields)

    def append(self, field: FieldDef) -> None:
        row = len(self._fields)
        self.beginInsertRows(QModelIndex(), row, row)
        self._fields.append(field)
        self.endInsertRows()

    def remove_row(self, row: int) -> None:
        if 0 <= row < len(self._fields):
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._fields[row]
            self.endRemoveRows()

    def show_all(self) -> None:
        if self._visible is not None:
            self._visible = None
            self.layoutChanged.emit()

    def hidden_count(self) -> int:
        return 0 if self._visible is None else max(0, len(self._fields) - self._visible)

    def field_names(self) -> set[str]:
        return {field.name for field in self._fields if field.name.strip()}


#: 取值方式的中文标签。**表现层的东西留在 GUI**（契约 `core/field_value_source.py` 只放结构），
#: 且必须过 `_()`（i18n 门禁：`tests/unit/gui/test_i18n_gate.py`）。
_POSITION_LABELS: dict[str, str] = {
    "child": _("子元素"),
    "element": _("元素自身文本"),
    "element_attr": _("元素自身属性"),
}


def position_label(key: str) -> str:
    """位置键 → 中文标签（未知键原样返回：将来加新位置也不会显示空白）。"""
    text = str(key)
    return _POSITION_LABELS.get(text, text)


def position_key(label_or_key: str) -> str | None:
    """中文标签**或**位置键 → 位置键；都不认识返回 ``None``。

    两种都接受是有意的：委托给的是下拉项（可能带标签），测试与配置给的是键，
    让两边都能用同一个入口，避免"哪边该翻译"这种约定散落。
    """
    text = str(label_or_key).strip()
    if not text:
        return None
    if text in POSITION_BY_KEY:
        return text
    for key, label in _POSITION_LABELS.items():
        if text == label:
            return key
    return None


#: 取值方式下拉的选项（键 + 标签），顺序即下拉顺序（来自契约的顺序）。
POSITION_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (key, label) for key, label in _POSITION_LABELS.items() if key in POSITION_BY_KEY
)


class FieldCellDelegate(QStyledItemDelegate):
    """字段表的单元格编辑器。

    * **取值方式**：下拉（选项来自契约，见 :data:`POSITION_CHOICES`）——
      这里是"选择"而不是自由文本，写错就会静默变成另一种取值语义；
    * **属性**：**可编辑**下拉 —— 候选是常见值载体属性（``COMMON_ATTRIBUTES``），
      但允许手输自定义属性（``data-*`` 也可能正是要采的值）。
    """

    def createEditor(  # noqa: N802 — Qt 命名
        self, parent: QWidget, option: Any, index: QModelIndex | QPersistentModelIndex
    ) -> QWidget:
        column = index.column()
        if column == FieldTableModel.COLUMN_POSITION:
            combo = QComboBox(parent)
            for key, label in POSITION_CHOICES:
                combo.addItem(label, key)
            return combo
        if column == FieldTableModel.COLUMN_ATTRIBUTE:
            combo = QComboBox(parent)
            combo.setEditable(True)
            for name in COMMON_ATTRIBUTES:
                combo.addItem(name, name)
            combo.setCurrentText("")
            return combo
        return super().createEditor(parent, option, index)

    def setEditorData(  # noqa: N802 — Qt 命名
        self, editor: QWidget, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        if isinstance(editor, QComboBox):
            value = index.data(Qt.ItemDataRole.EditRole)
            position = editor.findData(value)
            if position >= 0:
                editor.setCurrentIndex(position)
            elif editor.isEditable():
                editor.setCurrentText(str(value or ""))
            return
        super().setEditorData(editor, index)

    def setModelData(  # noqa: N802 — Qt 命名
        self, editor: QWidget, model: Any, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        if isinstance(editor, QComboBox):
            data = editor.currentData()
            value = data if data is not None else editor.currentText()
            model.setData(index, value, Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)


# ---------------------------------------------------------------------------
# UI primitives extracted from task_canvas.py (P1-3 first split)
# ---------------------------------------------------------------------------

class _Section(QGroupBox):
    """可折叠区域容器：标题 + 折叠按钮 + 内容。

    ``sticky`` 为折叠时仍常驻显示的状态条（如验证区试跑状态栏，
    满足 PRD §2.4「验证区永不消失」）；其余 body 内容折叠时隐藏。
    """

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        sticky: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAccessibleName(str(title))
        self._collapsed = False
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("sectionTitle")
        header.addWidget(self._title_label)
        header.addStretch()
        self._fold_btn = QPushButton(_("收起"))
        self._fold_btn.setObjectName("foldBtn")
        self._fold_btn.setFlat(True)
        self._fold_btn.clicked.connect(self._toggle)
        header.addWidget(self._fold_btn)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING["lg"], SPACING["sm"], SPACING["lg"], SPACING["md"])
        outer.addLayout(header)
        self._body_host = QWidget()
        self._body = QVBoxLayout(self._body_host)
        self._body.setSpacing(SPACING["md"])
        outer.addWidget(self._body_host)
        self._sticky_widget = sticky
        if sticky is not None:
            outer.addWidget(sticky)

    def body(self) -> QVBoxLayout:
        return self._body

    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._fold_btn.setText(_("展开") if self._collapsed else _("收起"))
        # 折叠只隐藏 body 内容；sticky 状态条保持常驻
        self._body_host.setVisible(not self._collapsed)
        self.toggled.emit(self._collapsed)
        repolish_widget(self)

    def collapsed(self) -> bool:
        return self._collapsed


def _form_row(
    label_text: str,
    widget: QWidget,
    help_id: str | None = None,
) -> QHBoxLayout:
    row = QHBoxLayout()
    if help_id:
        row.addWidget(HelpTooltip(help_id))
    label = QLabel(label_text)
    label.setObjectName("muted")
    row.addWidget(label)
    row.addStretch()
    row.addWidget(widget)
    return row
