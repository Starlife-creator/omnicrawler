from pathlib import Path

from tools.release_delta_baselines import select_baselines


def test_branch_build_excludes_same_and_future_versions():
    assert select_baselines(["v0.15.0", "v0.16.0", "v0.14.0", "v0.13.1", "v0.13.1", "v0.12.0", "v0.11.0", "v0.15.0rc1"],
                            "0.15.0", enabled=True) == ["0.14.0", "0.13.1", "0.12.0"]


def test_manual_validation_has_no_delta_by_default():
    assert select_baselines(["v0.14.0"], "0.15.0", enabled=False) == []
    workflow = (Path(__file__).resolve().parents[3] / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "generate_delta_packages:" in workflow
    assert "DELTA_REQUESTED:" in workflow and '"$DELTA_REQUESTED" == "true"' in workflow
    assert "python tools/release_delta_baselines.py" in workflow


def test_numeric_order_and_empty_history():
    assert select_baselines(["v0.9.0", "v0.10.0"], "0.11.0", enabled=True) == ["0.10.0", "0.9.0"]
    assert select_baselines([], "0.15.0", enabled=True) == []
