"""Reusable worker and model components for :mod:`task_canvas`."""

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
from PySide6.QtWidgets import QWidget

from ..core.config_model import FieldDef
from ..i18n import _


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

        return provider_from_env(project_root=self._project_root)


class FieldTableModel(QAbstractTableModel):
    """Editable, progressively disclosed field model for the task canvas."""

    _HEADERS: tuple[str, ...] = (_("名称"), _("选择器"), _("类型"))
    _INITIAL_VISIBLE_ROWS = 10

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
        return 3

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:  # type: ignore[override]
        if not index.isValid() or not 0 <= index.row() < len(self._fields):
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        field = self._fields[index.row()]
        return (field.name, field.selector, field.selector_type)[index.column()]

    def headerData(
        self,
        section: int,
        orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section] if section < len(self._HEADERS) else None
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # type: ignore[override]
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:  # type: ignore[override]
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        field = self._fields[index.row()]
        text = str(value)
        if index.column() == 0:
            if not text.strip():
                return False
            field.name = text.strip()
        elif index.column() == 1:
            field.selector = text
        else:
            field.selector_type = (
                cast(Literal["css", "xpath", "jsonpath"], text)
                if text in ("css", "xpath", "jsonpath")
                else "css"
            )
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
