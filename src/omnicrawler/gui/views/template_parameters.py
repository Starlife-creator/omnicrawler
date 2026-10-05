"""Template input widgets use the same declared validation as CLI rendering."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...templates.parameters import validate_parameters
from ..i18n import _


class TemplateParametersDialog(QDialog):
    def __init__(self, declarations: Mapping[str, Any], initial: Mapping[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("模板参数"))
        self.setAccessibleName(_("模板参数与约束"))
        self.resize(560, 460)
        self.declarations = declarations
        self.inputs: dict[str, QLineEdit | QComboBox] = {}
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(_("填写当前任务参数；通过校验后仍需检查组合预览和试跑。")))
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        form = QFormLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        for name, declaration in declarations.items():
            spec = declaration if isinstance(declaration, Mapping) else {"default": declaration}
            default = initial.get(name, spec.get("default"))
            control: QLineEdit | QComboBox
            if "enum" in spec or spec.get("type") == "boolean":
                control = QComboBox()
                control.addItem(_("未填写"), None)
                for value in spec.get("enum", [True, False]):
                    control.addItem(json.dumps(value, ensure_ascii=False), value)
                    if type(default) is type(value) and default == value:
                        control.setCurrentIndex(control.count() - 1)
            else:
                control = QLineEdit()
                control.setMaxLength(20000)
                if default is not None:
                    control.setText(json.dumps(default, ensure_ascii=False) if isinstance(default, (list, dict)) else str(default))
                if spec.get("sensitive"):
                    control.setEchoMode(QLineEdit.EchoMode.Password)
                control.setPlaceholderText(str(spec.get("description", spec.get("type", ""))))
            control.setAccessibleName(name + (_("（必填）") if spec.get("required") else ""))
            control.setToolTip(str(spec.get("description", "")))
            self.inputs[name] = control
            form.addRow(name + (" *" if spec.get("required") else ""), control)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        values = {name: control.currentData() if isinstance(control, QComboBox) else control.text()
                  for name, control in self.inputs.items()}
        return validate_parameters(self.declarations, values, strict=True)

    def _validate(self) -> None:
        try:
            self.values()
        except ValueError as exc:
            QMessageBox.warning(self, _("参数无效"), str(exc))
            return
        self.accept()
