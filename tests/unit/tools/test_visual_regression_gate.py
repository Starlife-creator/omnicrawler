"""Visual gate must reject empty/skipped evidence and keep reference loading explicit."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.check_visual_regression import run_snapshot


@pytest.mark.parametrize("tests,skipped,code", [(0, 0, 0), (1, 1, 0), (1, 0, 1)])
def test_visual_gate_rejects_incomplete_result(tmp_path, monkeypatch, tests, skipped, code):
    def execute(command, **kwargs):
        xml = next(arg.removeprefix("--junitxml=") for arg in command if arg.startswith("--junitxml="))
        from pathlib import Path
        Path(xml).write_text(f'<testsuites><testsuite tests="{tests}" failures="0" errors="0" skipped="{skipped}"/></testsuites>', encoding="utf8")
        assert kwargs["env"]["PYTHONPATH"] == str(tmp_path / "src")
        assert "OMNI_BASELINE" not in kwargs["env"]
        return SimpleNamespace(returncode=code)
    monkeypatch.setenv("OMNI_BASELINE", "1")
    monkeypatch.setattr("tools.check_visual_regression.subprocess.run", execute)
    with pytest.raises(RuntimeError, match="visual regression failed"):
        run_snapshot(tmp_path / "src", tmp_path, generate=False)
