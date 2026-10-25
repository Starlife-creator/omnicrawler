from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_checker(project_root: Path):
    checker_path = project_root / "tools" / "check_sdk_api.py"
    spec = importlib.util.spec_from_file_location("check_sdk_api", checker_path)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    return checker


def test_versioned_sdk_public_api_snapshot() -> None:
    project_root = Path(__file__).resolve().parents[3]
    checker = _load_checker(project_root)
    assert checker.check(project_root) == []


def test_application_version_comes_from_version_leaf_module() -> None:
    """C-#22：应用版本真源是 ``src/omnicrawler/_version.py``，与 SDK 契约版本分开。"""
    project_root = Path(__file__).resolve().parents[3]
    checker = _load_checker(project_root)
    assert checker._application_version(project_root) == "1.0.1"


def test_application_version_drift_is_reported(tmp_path: Path) -> None:
    """schema 登记的应用版本与 ``_version.py`` 不一致时必须判红（C-#22 一致性校验）。"""
    project_root = Path(__file__).resolve().parents[3]
    checker = _load_checker(project_root)

    schema = json.loads(
        (project_root / "schemas" / "sdk-public-api.json").read_text(encoding="utf-8")
    )
    schema["application_version"] = "9.9.9"  # 人为漂移

    root = tmp_path / "repo"
    (root / "schemas").mkdir(parents=True)
    (root / "schemas" / "sdk-public-api.json").write_text(
        json.dumps(schema), encoding="utf-8"
    )
    (root / "src" / "omnicrawler" / "sdk").mkdir(parents=True)
    (root / "src" / "omnicrawler" / "sdk" / "__init__.py").write_text(
        'SDK_VERSION = "1.0-preview"\n__all__ = []\nAPI_STABILITY = {}\n',
        encoding="utf-8",
    )
    (root / "src" / "omnicrawler" / "plugins").mkdir(parents=True)
    (root / "src" / "omnicrawler" / "plugins" / "plugin_sdk.py").write_text(
        "__all__ = []\n", encoding="utf-8"
    )
    (root / "src" / "omnicrawler" / "_version.py").write_text(
        '__version__ = "1.0.1"\n', encoding="utf-8"
    )

    errors = checker.check(root)
    assert any("application_version" in error for error in errors), errors
