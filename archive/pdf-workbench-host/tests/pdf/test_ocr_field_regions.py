import pytest

from omnicrawler.pdfx.extraction import _observable_confidence
from omnicrawler.pdfx.ocr_result import field_region
from omnicrawler.pdfx.retrieval import CandidatePage


def test_field_confidence_uses_its_regions_instead_of_whole_page():
    structure = {"words": [{"text": "金额", "confidence": .99, "bbox": [0, 0, 10, 10]},
                           {"text": "123", "confidence": .3, "bbox": [10, 0, 20, 10]},
                           {"text": "编号ABC", "confidence": .98, "bbox": [0, 20, 20, 30]}]}
    page = CandidatePage(1, "金额123 编号ABC", 1, "ocr", .95, structure)
    amount = {"raw_value": "123", "page_no": 1, "extraction_method": "content_rule", "matched_by_pattern": True}
    assert _observable_confidence(amount, {1: page}) == pytest.approx(.294)
    assert _observable_confidence({**amount, "raw_value": "ABC"}, {1: page}) == pytest.approx(.9604)


def test_ambiguous_or_unmapped_field_never_borrows_page_score():
    page = CandidatePage(1, "123 123", 1, "ocr", .99,
                         {"words": [{"text": "123", "confidence": .99}, {"text": "123", "confidence": .99}]})
    assert field_region("123", page.ocr_structure)["status"] == "ambiguous"
    value = {"raw_value": "123", "page_no": 1, "extraction_method": "content_rule", "matched_by_pattern": True}
    assert _observable_confidence(value, {1: page}) < .6
    assert _observable_confidence({**value, "raw_value": "unknown"}, {1: page}) < .6


def test_paddle_transformed_geometry_cannot_claim_original_image_mapping():
    from types import SimpleNamespace

    from omnicrawler.pdfx.ocr import PaddleStructureBackend
    backend = PaddleStructureBackend.__new__(PaddleStructureBackend)
    image = SimpleNamespace(size=(300, 600))
    image.convert = lambda mode: image
    backend.Image = SimpleNamespace(open=lambda stream: image)
    backend.np = SimpleNamespace(asarray=lambda value: value)
    backend.pipeline = SimpleNamespace(predict=lambda value: [SimpleNamespace(
        json={"res": {"overall_ocr_res": {"rec_texts": ["Hello"], "rec_boxes": [[0, 0, 10, 10]], "rec_scores": [.9]}}},
        markdown={})])
    backend.transforms_enabled = True
    result = backend.recognize_rich(b"image")
    assert result.metadata["coordinate_system"] == "ocr_engine_pixels_top_left"
    assert result.metadata["original_mapping"] == "unverified"
    assert result.metadata["original_image_size"] == [300, 600]
