"""S2.5.35：export 空库显式报错。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.services.application_service import ApplicationService


def test_export_without_database_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"project: {{name: exp, workspace: '{tmp_path / 'work'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    service = ApplicationService(config_path)
    with pytest.raises(FileNotFoundError, match="先运行采集任务"):
        service.export()
