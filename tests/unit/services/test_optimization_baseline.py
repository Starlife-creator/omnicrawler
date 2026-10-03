import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("baseline", Path(__file__).resolve().parents[3] / "tools/measure_optimization_baseline.py")
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


def test_inventory_is_repeatable_and_never_invents_runtime_results(tmp_path):
    root = tmp_path / "bundle"
    (root / "paddle").mkdir(parents=True)
    (root / "paddle/runtime.dll").write_bytes(b"0123456789")
    (root / "core.dll").write_bytes(b"core")
    before = _module.inventory(root, label="fixture")
    assert before == _module.inventory(root, label="fixture")
    assert before["disk_bytes"] == 14
    assert before["groups"]["paddle"] == 10
    assert "process_tree_peak_bytes" in before["unknown_metrics"]
    assert not before["content_hash_verified"]
    (root / "paddle/runtime.dll").write_bytes(b"changed length")
    assert before["metadata_sha256"] != _module.inventory(root, label="fixture")["metadata_sha256"]


def test_empty_inventory_is_not_a_successful_baseline(tmp_path):
    with pytest.raises(ValueError, match="empty inventory"):
        _module.inventory(tmp_path, label="empty")
