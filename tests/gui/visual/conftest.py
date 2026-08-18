"""Fixtures for visual regression tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from . import BASELINE_DIR, TOLERANCE


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication with dark/light/high_contrast switching."""
    import sys

    from PyQt6.QtWidgets import QApplication

    os.environ.setdefault("OMNICRAWL_SKIP_FIRST_LAUNCH", "1")
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    app.quit()


@pytest.fixture
def theme_manager(qapp):
    """Return the singleton ThemeManager, pre-applied on the QApplication."""
    from omnicrawler.gui.design_system import ThemeManager

    mgr = ThemeManager.instance()
    mgr.apply(qapp, "light")
    yield mgr
    # Reset singleton to prevent stale C++ references across test sessions
    ThemeManager.reset()


def _snapshot_path(theme: str, widget_name: str) -> Path:
    return BASELINE_DIR / theme / f"{widget_name}.png"


def compare_snapshot(widget_name: str, theme: str, pixmap) -> dict[str, object]:
    """Compare *pixmap* against stored baseline, return diff dict.

    Returns ``{"match": True}`` or ``{"match": False, "diff_pct": 0.05}``.
    """
    import io

    from PIL import Image, ImageChops

    path = _snapshot_path(theme, widget_name)
    if os.environ.get("OMNI_BASELINE"):
        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(path), "PNG")
        return {"match": True, "action": "baseline_saved"}

    if not path.is_file():
        return {"match": False, "reason": "no_baseline", "expected_path": str(path)}

    img_bytes = io.BytesIO()
    pixmap.save(img_bytes, "PNG")
    current = Image.open(img_bytes).convert("RGB")
    baseline = Image.open(path).convert("RGB")

    if current.size != baseline.size:
        return {"match": False, "reason": "size_mismatch",
                "current": current.size, "baseline": baseline.size}

    diff = ImageChops.difference(current, baseline)
    diff_pct = sum(1 for px in diff.getdata() if px != (0, 0, 0)) / (diff.size[0] * diff.size[1])
    return {"match": diff_pct <= TOLERANCE, "diff_pct": round(diff_pct, 4)}
