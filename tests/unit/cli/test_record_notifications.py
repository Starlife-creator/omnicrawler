import json

import pytest

from omnicrawler.cli import _handlers
from omnicrawler.cli._main import build_parser
from omnicrawler.core.safe_action import ConfirmationRequiredError


def test_notification_command_reads_state_without_sending_and_requires_explicit_retry(tmp_path, monkeypatch, capsys):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: notices, workspace: work}\nsource: {kind: rest, seeds: [https://example.test/items]}\n", encoding="utf8")
    parser = build_parser()
    args = parser.parse_args(["notifications", "--config", str(path), "report"])
    _handlers._run_notifications(args)
    assert json.loads(capsys.readouterr().out) == {"enabled": False, "deliveries": []}
    args = parser.parse_args(["notifications", "--config", str(path), "retry", "--event-id", "chosen"])
    monkeypatch.setattr("sys.argv", ["omnicrawler", "notifications", "retry"])
    with pytest.raises(ConfirmationRequiredError):
        _handlers._run_notifications(args)
