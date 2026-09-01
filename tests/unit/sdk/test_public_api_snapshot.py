from __future__ import annotations

import importlib.util
from pathlib import Path


def test_versioned_sdk_public_api_snapshot() -> None:
    project_root = Path(__file__).resolve().parents[3]
    checker_path = project_root / "tools" / "check_sdk_api.py"
    spec = importlib.util.spec_from_file_location("check_sdk_api", checker_path)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert checker.check(project_root) == []
