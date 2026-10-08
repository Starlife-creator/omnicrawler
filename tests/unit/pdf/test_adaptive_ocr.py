"""Adaptive retries preserve alternatives, budgets and mandatory human review."""
import io
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from omnicrawler.pdfx.adaptive_ocr import AdaptiveOCRPolicy
from omnicrawler.pdfx.ocr import TesseractBackend, recognize_page
from omnicrawler.pdfx.ocr_result import field_region
from omnicrawler.pdfx.validation import validate_record


@pytest.fixture
def backend(monkeypatch):
    module = SimpleNamespace(Output=SimpleNamespace(DICT="dict"), pytesseract=SimpleNamespace(tesseract_cmd="test"))
    monkeypatch.setitem(sys.modules, "pytesseract", module)
    return TesseractBackend({"lang": "eng", "adaptive_retry": {"enabled": True, "minimum_characters": 20}})


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(buffer, "PNG")
    return buffer.getvalue()


def data(text, confidence=95):
    return {"text": [text], "block_num": [1], "par_num": [1], "line_num": [1],
            "left": [30], "top": [30], "width": [60], "height": [30], "conf": [confidence]}


def test_retry_recovers_more_text_keeps_alternatives_and_original_geometry(backend):
    calls = []
    outputs = [data("A", 20), data("Invoice A123 Amount 123.45 USD"), data("Invoice A123", 90)]

    def recognize(image, **options):
        calls.append((image.size, options))
        return outputs[len(calls)-1]

    backend.pytesseract.image_to_data = recognize
    result = recognize_page(backend, png())
    assert len(calls) == 3
    assert calls[1][0] == (300, 240) and calls[1][1]["config"] == "--psm 6"
    assert all(0 < call[1]["timeout"] <= 60 for call in calls)
    assert result.text == "Invoice A123 Amount 123.45 USD"
    assert result.words[0]["bbox"] == [10, 10, 30, 20]
    retry = result.metadata["adaptive_retry"]
    assert [a["text"] for a in retry["attempts"]] == [o["text"][0] for o in outputs]
    assert retry["selected_attempt"] == 1 and retry["disagreement"] and retry["review_required"]
    assert backend.image_scale == 1 and backend.psm == 3
    region = field_region("123.45", {"words": result.words, "metadata": result.metadata})
    assert region["adaptive_retry_review"] is True
    spec = SimpleNamespace(name="amount", label="金额", required=True, type="number", minimum=None,
                           maximum=None, value_pattern=None, allowed_values=[])
    config = SimpleNamespace(field_map=lambda: {"amount": spec}, validation={"auto_accept_confidence": 0}, fields=[spec])
    values = {"amount": {"raw_value": "123.45", "normalized_value": "123.45", "evidence": result.text,
                         "source_is_ocr": True, "source_ocr_confidence": .95, "confidence": .95, "ocr_region": region}}
    validation = validate_record(config, values, .95)
    assert validation.review_status == "needs_review"
    assert any("OCR重试" in message for message in validation.messages)


def test_strong_primary_does_not_launch_more_recognition(backend):
    calls = []
    def recognize(image, **options):
        calls.append(options)
        return data("Invoice A123 Amount 123.45 USD")
    backend.pytesseract.image_to_data = recognize
    result = recognize_page(backend, png())
    assert len(calls) == 1
    assert result.metadata["adaptive_retry"]["stop_reason"] == "primary_trigger_not_met"
    assert result.metadata["adaptive_retry"]["review_required"] is False


def test_failed_retry_keeps_usable_first_result(backend):
    calls = []
    def recognize(image, **options):
        calls.append(options)
        if len(calls) == 1:
            return data("A123", 20)
        raise RuntimeError("engine timeout")
    backend.pytesseract.image_to_data = recognize
    result = recognize_page(backend, png())
    assert result.text == "A123"
    assert len(calls) == 3
    assert "error" in result.metadata["adaptive_retry"]["attempts"][1]
    assert result.metadata["adaptive_retry"]["review_required"] is True


def test_shared_time_budget_stops_without_extra_ocr(backend, monkeypatch):
    ticks = iter([0., 0., 61.])
    monkeypatch.setattr("omnicrawler.pdfx.adaptive_ocr.time.monotonic", lambda: next(ticks))
    calls = []
    def recognize(image, **options):
        calls.append(options)
        return data("A", 10)
    backend.pytesseract.image_to_data = recognize
    result = recognize_page(backend, png())
    assert len(calls) == 1 and calls[0]["timeout"] == 60
    assert result.metadata["adaptive_retry"]["stop_reason"] == "time_budget_exhausted"


@pytest.mark.parametrize("invalid", [{"enabled": "true"}, {"maximum_attempts": 0}, {"maximum_attempts": True},
    {"total_timeout_seconds": float("nan")}, {"total_timeout_seconds": 0}, {"total_timeout_seconds": 121},
    {"minimum_confidence": 2}, {"minimum_characters": -1}, {"profiles": []}, {"unknown": True},
    {"profiles": [{"image_scale": 5, "page_segmentation_mode": 6}]}])
def test_invalid_retry_configuration_is_rejected_before_recognition(invalid):
    with pytest.raises(ValueError):
        AdaptiveOCRPolicy.from_config({"enabled": True, **invalid})


@pytest.mark.parametrize("name,component", [("paddle", None), ("none", None), ("tesseract", "offline-package")])
def test_unsupported_runtime_cannot_silently_ignore_retry_policy(name, component):
    from omnicrawler.pdfx.config import validate_runtime_config
    config = SimpleNamespace(ocr={"backend": name, "component": component, "adaptive_retry": {"enabled": True}})
    with pytest.raises(ValueError, match="仅支持本机 Tesseract"):
        validate_runtime_config(config)
