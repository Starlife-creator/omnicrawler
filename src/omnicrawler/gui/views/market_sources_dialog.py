"""市场索引源管理对话框（#77 Phase 1）。

职责单一：把 `commands/plugins_catalog.py` 的 list/add/remove 变成可点的界面。
**不在这里写任何 YAML**——增删与启用/停用/优先级都走同一条命令层路径（与 CLI 一致），
避免 GUI 与 CLI 各写一套配置逻辑而漂移。

信任信号分级在列表里直接可见（官方策展 / 社区索引未审核），并明示"隐式官方源"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ...commands.plugins_catalog import catalogs_add, catalogs_list, catalogs_remove
from ..i18n import _
from ..widgets.toast import ToastManager

_TRUST_LABEL = {
    "official": _("官方策展"),
    "community": _("社区索引（未经官方审核）"),
}


class MarketSourcesDialog(QDialog):
    """列出 / 添加 / 移除 / 启用停用 / 调整优先级。"""

    def __init__(self, config_path: Path | str, parent: Any = None) -> None:
        super().__init__(parent)
        self._config_path = str(config_path)
        self.setWindowTitle(_("管理市场索引源"))
        self.setAccessibleName(_("管理市场索引源"))
        self.resize(720, 420)
        self._setup_ui()
        self.reload()

    # ── UI ────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        self._status = QLabel(_("正在读取索引配置…"))
        self._status.setAccessibleName(_("索引配置状态"))
        root.addWidget(self._status)

        self._list = QListWidget()
        self._list.setAccessibleName(_("已配置的市场索引列表"))
        root.addWidget(self._list, 1)

        # 添加行：URL + 类型 + 优先级
        add_row = QHBoxLayout()
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(_("索引 URL（如 https://example.com/my-catalog）"))
        self._url_edit.setAccessibleName(_("新索引 URL"))
        self._kind_box = QComboBox()
        self._kind_box.setAccessibleName(_("新索引类型"))
        for value, label in (
            ("community", _("社区索引")),
            ("topic", _("主题聚合")),
            ("curated", _("官方策展（自建官方源时才选）")),
        ):
            self._kind_box.addItem(label, value)
        self._priority_box = QSpinBox()
        self._priority_box.setRange(0, 9999)
        self._priority_box.setValue(100)
        self._priority_box.setAccessibleName(_("新索引优先级"))
        add_btn = QPushButton(_("添加"))
        add_btn.setAccessibleName(_("添加索引"))
        add_btn.clicked.connect(self._on_add)
        add_row.addWidget(self._url_edit, 1)
        add_row.addWidget(self._kind_box)
        add_row.addWidget(self._priority_box)
        add_row.addWidget(add_btn)
        root.addLayout(add_row)

        action_row = QHBoxLayout()
        for label, slot in (
            (_("启用/停用"), self._on_toggle),
            (_("优先级 ↑"), lambda: self._on_priority(-10)),
            (_("优先级 ↓"), lambda: self._on_priority(10)),
            (_("移除"), self._on_remove),
        ):
            button = QPushButton(label)
            button.setAccessibleName(label)
            button.clicked.connect(slot)
            action_row.addWidget(button)
        action_row.addStretch(1)
        close_btn = QPushButton(_("关闭"))
        close_btn.setAccessibleName(_("关闭"))
        close_btn.clicked.connect(self.accept)
        action_row.addWidget(close_btn)
        root.addLayout(action_row)

        hint = QLabel(
            _(
                "说明：官方策展索引由维护者审核；社区索引未经官方审核，安装仍会校验创作者签名。"
                "优先级小的先展示；停用的索引不参与聚合但配置保留。"
            )
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

    # ── 行为 ──────────────────────────────────────────────
    def reload(self) -> None:
        payload, _code = catalogs_list(config_path=self._config_path)
        self._list.clear()
        sources = payload.get("sources") or []
        for source in sources:
            trust = _TRUST_LABEL.get(str(source.get("trust")), str(source.get("trust")))
            state = _("启用") if source.get("enabled") else _("停用")
            label = (
                f"{source.get('url', '')}　[{source.get('kind', '')} · {trust}]　"
                f"{_('优先级')}={source.get('priority', '')}　{state}"
            )
            if source.get("implicit"):
                label += _("　（隐式官方源）")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(source.get("url", "")))
            item.setToolTip(
                _("来源：{0}").format(trust)
                + ("\n" + _("该来源来自 catalog_url 的兼容路径，未显式配置。")
                   if source.get("implicit") else "")
            )
            self._list.addItem(item)
        self._status.setText(
            str(payload.get("detail", ""))
            + ("" if payload.get("explicit") else "\n" + _("提示：添加任意索引后即进入多源模式。"))
        )

    def _current_url(self) -> str:
        item = self._list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""

    def _notify(self, payload: dict[str, Any], code: int) -> None:
        message = str(payload.get("detail", ""))
        if payload.get("status") == "ok":
            ToastManager.instance().success(message)
        else:
            ToastManager.instance().warning(message)
        self.reload()

    def _on_add(self) -> None:
        url = self._url_edit.text().strip()
        if not url:
            ToastManager.instance().info(_("请先填写索引 URL"))
            return
        payload, code = catalogs_add(
            config_path=self._config_path,
            url=url,
            kind=str(self._kind_box.currentData()),
            priority=int(self._priority_box.value()),
        )
        if payload.get("status") == "ok":
            self._url_edit.clear()
        self._notify(payload, code)

    def _on_remove(self) -> None:
        url = self._current_url()
        if not url:
            ToastManager.instance().info(_("请先选择一个索引"))
            return
        payload, code = catalogs_remove(config_path=self._config_path, url=url)
        self._notify(payload, code)

    def _on_toggle(self) -> None:
        url = self._current_url()
        if not url:
            ToastManager.instance().info(_("请先选择一个索引"))
            return
        payload, _code = catalogs_list(config_path=self._config_path)
        current = next(
            (s for s in payload.get("sources") or [] if s.get("url") == url), None
        )
        if current is None:
            ToastManager.instance().warning(_("该索引已不在配置中"))
            self.reload()
            return
        result, code = catalogs_add(
            config_path=self._config_path,
            url=url,
            kind=str(current.get("kind") or "community"),
            priority=int(current.get("priority") or 100),
            enabled=not bool(current.get("enabled")),
        )
        self._notify(result, code)

    def _on_priority(self, delta: int) -> None:
        url = self._current_url()
        if not url:
            ToastManager.instance().info(_("请先选择一个索引"))
            return
        payload, _code = catalogs_list(config_path=self._config_path)
        current = next(
            (s for s in payload.get("sources") or [] if s.get("url") == url), None
        )
        if current is None:
            ToastManager.instance().warning(_("该索引已不在配置中"))
            self.reload()
            return
        result, code = catalogs_add(
            config_path=self._config_path,
            url=url,
            kind=str(current.get("kind") or "community"),
            priority=max(0, int(current.get("priority") or 100) + delta),
            enabled=bool(current.get("enabled")),
        )
        self._notify(result, code)
