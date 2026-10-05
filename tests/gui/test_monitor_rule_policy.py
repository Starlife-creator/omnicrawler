import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from omnicrawler.gui.views.change_monitor import NewRuleDialog


def test_extended_rule_fields_round_trip_without_losing_legacy_values():
    app = QApplication.instance() or QApplication([])
    rule = {"name": "price", "url": "https://example.org", "rule_id": "one",
            "consecutive_checks": 3, "cooldown_seconds": 60, "minimum_relative_change": 0.1,
            "minimum_absolute_change": 2, "ignored_selectors": [".clock", ".banner"],
            "webhook_url": "https://example.org/hook", "webhook_token_ref": "secret://HOOK_TOKEN"}
    dialog = NewRuleDialog(rule_data=rule)
    saved = dialog.get_rule_data()
    for key, value in rule.items():
        assert saved[key] == value
    dialog.close()
    app.processEvents()
