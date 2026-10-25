"""B-P2-8：损坏 OCR 结构 JSON 走 safe_json_loads 降级，检索/导出不整体中断。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from omnicrawler.pdfx.database import Database
from omnicrawler.pdfx.retrieval import select_candidates
from omnicrawler.pdfx.text_export import export_text_stage


class _FakeConnection:
    def execute(self, *_args: object, **_kwargs: object) -> None:
        return None


class _FakeTransaction:
    def __enter__(self) -> _FakeConnection:
        return _FakeConnection()

    def __exit__(self, *_args: object) -> None:
        return None


def test_select_candidates_degrades_malformed_ocr_structure() -> None:
    config = SimpleNamespace(
        fields=[],
        retrieval={
            "negative_keywords": [],
            "top_pages": 3,
            "min_score": 0.0,
            "neighbor_pages": 1,
            "fallback_pages": [1],
        },
    )
    db = SimpleNamespace(
        fetchall=lambda _sql, _params: [
            {
                "page_no": 1,
                "final_text": "hello world",
                "parse_method": "native",
                "ocr_confidence": None,
                "ocr_structure_json": "{broken-json",
            },
        ],
        transaction=lambda: _FakeTransaction(),
    )
    result = select_candidates(config, db, "doc-1")
    assert len(result) == 1
    assert result[0].ocr_structure == {}


def test_text_export_counts_malformed_structure_and_continues(tmp_path: Path) -> None:
    config = SimpleNamespace(output_dir=tmp_path / "output")
    db = Database(tmp_path / "state.db")
    try:
        db.execute(
            "INSERT INTO documents(doc_id,sha256,primary_path,filename,size_bytes,created_at,updated_at) "
            "VALUES('doc','h','fixture.pdf','fixture.pdf',1,'now','now')"
        )
        db.execute(
            "INSERT INTO pages(doc_id,page_no,final_text,parse_method,ocr_structure_json,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            ("doc", 1, "Plain text", "native", "{broken-json", "now"),
        )
        summary = export_text_stage(config, db)
        assert summary["skipped_malformed"] == 1
        assert summary["pages"] == 1
        row = (config.output_dir / "pages.jsonl").read_text(encoding="utf-8").strip()
        import json
        assert json.loads(row)["ocr_structure"] is None
    finally:
        db.close()
