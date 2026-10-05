import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.views.template_parameters import TemplateParametersDialog
from omnicrawler.templates.template_application import apply_template


def test_parameter_form_preserves_json_types_and_enforces_constraints():
    app = QApplication.instance() or QApplication([])
    dialog = TemplateParametersDialog({"count": {"type": "integer", "minimum": 1, "required": True},
                                       "enabled": {"type": "boolean", "default": False},
                                       "tags": {"type": "array", "default": ["one"]}}, {})
    dialog.inputs["count"].setText("3")
    assert dialog.values() == {"count": 3, "enabled": False, "tags": ["one"]}
    dialog.inputs["count"].setText("true")
    with pytest.raises(ValueError):
        dialog.values()
    dialog.close()
    app.processEvents()


def test_capability_composition_keeps_task_identity_and_existing_choices():
    current = {"project": {"name": "user", "task_id": "stable", "workspace": "work/user"},
               "source": {"seeds": ["https://user.example"]}, "extract": {"fields": {"title": "h1"}}}
    recipe = {"project": {"name": "template", "task_id": "foreign"}, "source": {"seeds": ["https://template.example"]},
              "download": {"enabled": True}}
    result = apply_template(current, recipe, preserve_choices=True)
    assert result.after["project"] == current["project"]
    assert result.after["source"]["seeds"] == current["source"]["seeds"]
    assert result.after["download"]["enabled"] is True
