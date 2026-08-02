"""Visual regression test framework for OmniCrawler GUI components.

Captures screenshots of key widgets across all three themes,
compares against stored baselines, and flags pixel-level differences.

Prerequisites::

    pip install omnicrawl-platform[gui] Pillow

Directory structure::

    tests/gui/
    ├── visual/
    │   ├── __init__.py
    │   ├── conftest.py          # fixture: qapp with theme switching
    │   ├── test_snapshots.py    # actual snapshot tests
    │   └── baselines/           # committed reference images
    │       ├── light/
    │       ├── dark/
    │       └── high_contrast/

Usage::

    # Capture new baselines
    OMNI_BASELINE=1 pytest tests/gui/visual/ -v

    # Compare against stored baselines (CI)
    pytest tests/gui/visual/ -v

    # Update baselines after intentional changes
    OMNI_BASELINE=1 pytest tests/gui/visual/ -v --force-baseline
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE_DIR = HERE / "baselines"
TOLERANCE = 0.01  # 1% pixel difference threshold
