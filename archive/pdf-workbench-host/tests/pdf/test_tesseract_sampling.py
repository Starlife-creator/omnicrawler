import io
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from omnicrawler.pdfx.ocr import TesseractBackend, recognize_page


@pytest.fixture(autouse=True)
def _recognition_dependency(monkeypatch):
    # 识别结果由测试提供，保留真实图像缩放与坐标计算，不要求核心安装包含 OCR。
    module = SimpleNamespace(Output=SimpleNamespace(DICT="dict"),
                             pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
                             image_to_data=lambda *args, **kwargs: pytest.fail("unexpected OCR call"))
    monkeypatch.setitem(sys.modules, "pytesseract", module)


def test_enlarged_ocr_coordinates_still_refer_to_original_image(monkeypatch):
    backend = TesseractBackend({"lang": "eng", "image_scale": 3, "page_segmentation_mode": 6})
    calls = []

    def recognize(image, **options):
        calls.append((image.size, options))
        return {"text": ["USD"], "block_num": [1], "par_num": [1], "line_num": [1],
                "left": [30], "top": [60], "width": [90], "height": [30], "conf": [90]}

    monkeypatch.setattr(backend.pytesseract, "image_to_data", recognize)
    buffer = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(buffer, "PNG")
    result = recognize_page(backend, buffer.getvalue())
    assert calls[0][0] == (300, 240) and calls[0][1]["config"] == "--psm 6"
    assert result.words[0]["bbox"] == [10, 20, 40, 30]
    assert result.metadata["original_image_size"] == [100, 80]
    assert result.metadata["original_mapping"] == "identity"
    assert result.text == "USD" and result.confidence == 0.9


@pytest.mark.parametrize("options", [{"image_scale": True}, {"image_scale": 5}, {"page_segmentation_mode": 0}, {"page_segmentation_mode": "6"}])
def test_invalid_sampling_is_rejected_before_ocr(options):
    with pytest.raises(ValueError):
        TesseractBackend(options)


def test_scaling_budget_rejects_before_allocating_or_invoking_ocr(monkeypatch):
    backend = TesseractBackend({"image_scale": 4})
    monkeypatch.setattr(backend.pytesseract, "image_to_data", lambda *args, **kwargs: pytest.fail("OCR invoked over budget"))
    buffer = io.BytesIO()
    Image.new("RGB", (2000, 1000), "white").save(buffer, "PNG")
    with pytest.raises(ValueError, match="像素预算"):
        backend.recognize_rich(buffer.getvalue())


def test_configured_tesseract_command_is_forwarded_without_running_ocr(monkeypatch):
    monkeypatch.setenv('TESSERACT_CMD', 'environment-tesseract')
    backend = TesseractBackend({})
    assert backend.pytesseract.pytesseract.tesseract_cmd == 'environment-tesseract'
    configured = TesseractBackend({'command': 'configured-tesseract'})
    assert configured.pytesseract.pytesseract.tesseract_cmd == 'configured-tesseract'
