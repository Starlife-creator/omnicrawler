"""Searchable template-library dialog, isolated from the GUI composition root."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .core.template_loader import TemplateInfo
from .i18n import _
from .settings import make_qsettings


class TemplateLibraryDialog(QDialog):
    """Searchable category view that remains usable with hundreds of templates."""

    def __init__(self, templates: list[TemplateInfo], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("模板库"))
        self.resize(720, 520)
        self._templates = templates
        self.selected_template: TemplateInfo | None = None
        self._settings = make_qsettings("OmniCrawler", "GUIWorkbench")
        stored_favorites = self._settings.value("templates/favorites", [])
        if isinstance(stored_favorites, str):
            stored_favorites = [stored_favorites]
        favorites = stored_favorites if isinstance(stored_favorites, list) else []
        self._favorites = {str(value) for value in favorites}

        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText(_("搜索名称、说明或标签…"))
        self._category = QComboBox()
        self._category.addItem(_("全部分类"), "")
        for category in sorted({item.category for item in templates}):
            self._category.addItem(category, category)
        self._favorite_only = QCheckBox(_("只看收藏"))
        filters.addWidget(self._search, 1)
        filters.addWidget(self._category)
        filters.addWidget(self._favorite_only)
        layout.addLayout(filters)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        layout.addWidget(self._list, 1)
        self._description = QLabel()
        self._description.setWordWrap(True)
        self._description.setMinimumHeight(55)
        layout.addWidget(self._description)
        self._favorite_button = QPushButton(_("☆ 收藏/取消收藏"))
        self._favorite_button.clicked.connect(self._toggle_favorite)
        layout.addWidget(self._favorite_button)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        open_button = buttons.button(QDialogButtonBox.StandardButton.Open)
        assert open_button is not None
        open_button.setText(_("加载模板"))
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._search.textChanged.connect(self._refresh)
        self._category.currentIndexChanged.connect(self._refresh)
        self._favorite_only.toggled.connect(self._refresh)
        self._list.currentItemChanged.connect(self._show_description)
        self._list.itemDoubleClicked.connect(lambda _item: self._accept_selected())
        self._refresh()

    def _refresh(self) -> None:
        query = self._search.text().strip().casefold()
        category = str(self._category.currentData() or "")
        self._list.clear()
        templates = sorted(
            self._templates,
            key=lambda item: (
                item.template_id not in self._favorites,
                item.category,
                item.display_name,
            ),
        )
        for template in templates:
            haystack = " ".join(
                (template.name, template.description, template.category, *template.tags)
            ).casefold()
            if query and query not in haystack:
                continue
            if category and template.category != category:
                continue
            if self._favorite_only.isChecked() and template.template_id not in self._favorites:
                continue
            verified = (
                _(f"  ·  验证 {template.verified_at}")
                if template.verified_at
                else _("  ·  未标注验证日期")
            )
            item = QListWidgetItem(
                f"{template.display_name}  ·  {template.category}  ·  v{template.version}{verified}"
            )
            item.setText(("★ " if template.template_id in self._favorites else "☆ ") + item.text())
            item.setData(Qt.ItemDataRole.UserRole, template.template_id)
            item.setToolTip(template.description)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._description.setText(_("没有匹配的模板"))

    def _show_description(self, item: QListWidgetItem | None, _previous=None) -> None:
        template = self._find(item)
        if template:
            capabilities = "、".join(template.capabilities) or _("未声明")
            source = _("内置模板") if template.is_builtin else _("用户模板")
            self._description.setText(
                f"{template.description}\n"
                + _(f"适用：{template.recommended_when or '请结合目标网址试跑判断'}\n")
                + _(f"为什么推荐：{template.why or '由网址、页面结构、数据源和所需能力综合判断'}\n")
                + _(f"限制：{template.limitations or '未声明特殊限制'}\n")
                + _(f"能力：{capabilities}\n来源：{source}；文件：{template.filepath}")
            )
        else:
            self._description.setText("")

    def _find(self, item: QListWidgetItem | None) -> TemplateInfo | None:
        template_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next(
            (value for value in self._templates if value.template_id == template_id), None,
        )

    def _accept_selected(self) -> None:
        self.selected_template = self._find(self._list.currentItem())
        if self.selected_template is not None:
            self.accept()

    def _toggle_favorite(self) -> None:
        template = self._find(self._list.currentItem())
        if template is None:
            return
        if template.template_id in self._favorites:
            self._favorites.remove(template.template_id)
        else:
            self._favorites.add(template.template_id)
        self._settings.setValue("templates/favorites", sorted(self._favorites))
        self._refresh()
