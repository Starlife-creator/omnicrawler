"""Fixtures for visual regression tests."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from . import BASELINE_DIR, TOLERANCE


def _png_bytes(pixmap) -> bytes:
    """把 QPixmap 编码成 PNG 字节。

    ★ 必须走 ``QBuffer``：PySide6 的 ``QPixmap.save`` 只接受**文件名或 QIODevice**，
    传 Python 的 ``io.BytesIO`` 会当场抛 ``TypeError``。这正是本模块长期整块 skip
    掩盖住的缺陷 —— 生成基线（走文件名分支）永远正常，第一次做**对比**才炸。
    """
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not pixmap.save(buffer, "PNG"):
            raise RuntimeError("QPixmap 编码 PNG 失败")
    finally:
        buffer.close()
    return bytes(byte_array)


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication with dark/light/high_contrast switching."""
    import sys

    from PySide6.QtWidgets import QApplication

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
    from PIL import Image, ImageChops

    path = _snapshot_path(theme, widget_name)
    if os.environ.get("OMNI_BASELINE"):
        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(path), "PNG")
        return {"match": True, "action": "baseline_saved"}

    if not path.is_file():
        return {"match": False, "reason": "no_baseline", "expected_path": str(path)}

    current = Image.open(io.BytesIO(_png_bytes(pixmap))).convert("RGB")
    baseline = Image.open(path).convert("RGB")

    if current.size != baseline.size:
        return {"match": False, "reason": "size_mismatch",
                "current": current.size, "baseline": baseline.size}

    diff = ImageChops.difference(current, baseline)
    diff_pct = _diff_pct(diff)
    return {"match": diff_pct <= TOLERANCE, "diff_pct": round(diff_pct, 4)}


def _diff_pct(diff) -> float:
    """差异像素占比（按字节精确判定，不走 ``getdata``）。

    ``Image.getdata()` 已被 Pillow 标记弃用（14 起移除），且 ``convert("L")`` 会因
    加权把"仅单通道差 1"的像素算成相同 —— 这里直接按 RGB 三字节比对，语义精确。
    """
    raw = diff.tobytes()
    total = diff.size[0] * diff.size[1]
    if total == 0:
        return 0.0
    unchanged = sum(
        1 for index in range(0, len(raw), 3) if raw[index : index + 3] == b"\x00\x00\x00"
    )
    return (total - unchanged) / total
