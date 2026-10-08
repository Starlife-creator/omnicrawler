import sys
from types import SimpleNamespace

import pytest

from omnicrawler.pdfx.ocr import PaddleStructureBackend


@pytest.mark.parametrize("config, expected", [({}, False), ({"enable_mkldnn": False}, False), ({"enable_mkldnn": True}, True)])
def test_cpu_acceleration_is_explicit_and_passed_to_actual_constructor(monkeypatch, config, expected):
    captured = {}

    def constructor(**options):
        captured.update(options)
        return SimpleNamespace()

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PPStructureV3=constructor))
    PaddleStructureBackend(config)
    assert captured["enable_mkldnn"] is expected


@pytest.mark.parametrize("invalid", ["false", 0, None])
def test_invalid_acceleration_setting_cannot_silently_enable_it(monkeypatch, invalid):
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PPStructureV3=lambda **options: pytest.fail("invalid configuration reached model construction")))
    with pytest.raises(ValueError, match="enable_mkldnn"):
        PaddleStructureBackend({"enable_mkldnn": invalid})
