from types import SimpleNamespace

from omnicrawler.pdfx import ocr


def test_parallel_paddle_preflight_does_not_construct_a_parent_model(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    constructed = []
    monkeypatch.setattr(ocr, "create_backend", lambda cfg: constructed.append("parent") or object())
    # Exercise the production stage, not merely the preflight helper.
    class Database:
        def fetchall(self, *args):
            return [{"doc_id": "d", "page_no": 1, "primary_path": "a.pdf"}]
        def execute(self, *args):
            pass
        def add_error(self, *args):
            pass
    class UnavailablePool:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("worker allocation deliberately refused")
    monkeypatch.setattr(ocr, "adaptive_ocr_workers", lambda requested: 2)
    monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", UnavailablePool)
    result = ocr.ocr_stage(SimpleNamespace(ocr={"backend": "paddle"}), Database(), ocr_workers=2)
    assert result["skipped"] == 1
    assert constructed == []
