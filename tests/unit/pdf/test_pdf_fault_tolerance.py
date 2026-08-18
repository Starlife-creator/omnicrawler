"""S2.3 PDF 容错与多进程降级测试。

覆盖：S2.3.1 OCR 多进程预检/池崩溃降级、S2.3.2 LLM 构造容错、
S2.3.3 语言归一、S2.3.4 阶段隔离/GUI 失败识别、S2.3.5 计数与目录、
S2.3.6/7 类型白名单。
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawler.pdfx import ocr
from omnicrawler.pdfx.ocr import normalize_ocr_lang

# -- S2.3.1 OCR 降级 --------------------------------------------------------


class FakeDb:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.errors: list[str] = []
        self.ops: list[tuple[str, tuple]] = []

    def fetchall(self, _sql, *_args) -> list[dict]:
        return self.rows

    def fetchone(self, _sql, *_args) -> dict | None:
        if "COUNT(*) AS n" in _sql:
            return {"n": 1}
        return None

    def execute(self, sql: str, values: tuple) -> None:
        self.ops.append((sql, values))

    def add_error(self, doc_id: str, stage: str, exc: Exception) -> None:
        self.errors.append(f"{doc_id}:{stage}:{exc}")


def _ocr_config(backend: str = "paddle") -> SimpleNamespace:
    return SimpleNamespace(ocr={"backend": backend, "dpi": 150})


def _rows(n: int = 2) -> list[dict]:
    return [
        {"doc_id": f"doc{i}", "page_no": i, "primary_path": f"/p/{i}.pdf"}
        for i in range(1, n + 1)
    ]


def test_s231_serial_backend_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "create_backend", lambda _config: (_ for _ in ()).throw(RuntimeError("缺少PaddleOCR依赖")))
    db = FakeDb(_rows())
    summary = ocr.ocr_stage(_ocr_config(), db, ocr_workers=1)
    assert summary["selected"] == 2 and summary["skipped"] == 2 and summary["failed"] == 0
    assert len(db.errors) == 2
    assert any("缺少PaddleOCR依赖" in error for error in db.errors)


def test_s231_mp_precheck_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "adaptive_ocr_workers", lambda _n: 4)
    monkeypatch.setattr(ocr, "create_backend", lambda _config: (_ for _ in ()).throw(RuntimeError("缺少PaddleOCR依赖")))
    db = FakeDb(_rows())
    summary = ocr.ocr_stage(_ocr_config(), db, ocr_workers=4)
    assert summary["selected"] == 2 and summary["skipped"] == 2
    assert len(db.errors) == 2
    assert all("缺少PaddleOCR依赖" in error for error in db.errors)


def test_s231_mp_precheck_none_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "adaptive_ocr_workers", lambda _n: 4)
    monkeypatch.setattr(ocr, "create_backend", lambda _config: None)
    db = FakeDb(_rows())
    summary = ocr.ocr_stage(_ocr_config(backend="none"), db, ocr_workers=4)
    assert summary["skipped"] == 2 and summary["selected"] == 2
    assert db.errors == []


def test_s231_mp_pool_crash_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures.process import BrokenProcessPool

    monkeypatch.setattr(ocr, "adaptive_ocr_workers", lambda _n: 4)
    monkeypatch.setattr(ocr, "create_backend", lambda _config: SimpleNamespace())

    class Boom:
        def __init__(self, *_a, **_k) -> None:
            raise BrokenProcessPool("worker died")

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", Boom)
    db = FakeDb(_rows())
    summary = ocr.ocr_stage(_ocr_config(), db, ocr_workers=4)
    assert summary["selected"] == 2 and summary["skipped"] == 2
    assert len(db.errors) == 2
    assert any("OCR 多进程崩溃" in error for error in db.errors)


# -- S2.3.3 语言归一 --------------------------------------------------------


def test_s233_lang_normalization() -> None:
    assert normalize_ocr_lang("ch") == "chi_sim"
    assert normalize_ocr_lang("chi") == "chi_sim"
    assert normalize_ocr_lang("cn") == "chi_sim"
    assert normalize_ocr_lang("trad") == "chi_tra"
    assert normalize_ocr_lang("jp") == "jpn"
    assert normalize_ocr_lang("jap") == "jpn"
    assert normalize_ocr_lang("ch+eng") == "chi_sim+eng"
    assert normalize_ocr_lang("CH+en") == "chi_sim+en"
    assert normalize_ocr_lang("") == "chi_sim+eng"
    assert normalize_ocr_lang("deu+jpn") == "deu+jpn"
    assert normalize_ocr_lang("  ch + eng ") == "chi_sim+eng"


# -- S2.3.4 阶段隔离 + GUI 失败识别 ------------------------------------------


def _pdf_config(tmp_path: Path) -> Path:
    path = tmp_path / "fields.yaml"
    path.write_text(
        "project_name: test\n"
        f"input_dir: {tmp_path / 'input'}\n"
        f"work_dir: {tmp_path / 'work'}\n"
        f"output_dir: {tmp_path / 'output'}\n"
        f"database: {tmp_path / 'work' / 'pipeline.sqlite3'}\n"
        "parser: {workers: 1, min_native_chars: 5, max_garbled_ratio: 0.1}\n"
        "ocr: {backend: none}\n"
        "retrieval: {top_pages: 2, neighbor_pages: 0, min_score: 1, fallback_pages: [1]}\n"
        "llm: {provider: disabled}\n"
        "extraction: {workers: 1, max_chars_per_page: 10000}\n"
        "normalization: {}\n"
        "validation: {auto_accept_confidence: 0.9, required_together: []}\n"
        "fields:\n"
        "  - {name: amount, label: 金额, type: amount, source: content, target_unit: 元}\n",
        encoding="utf-8",
    )
    return path


def test_s234_ocr_stage_failure_is_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from omnicrawler.pdfx import service

    monkeypatch.setattr(service, "ocr_stage", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("OCR 崩溃")))
    monkeypatch.setattr(service, "ingest", lambda *_a, **_k: {"new": 0})
    monkeypatch.setattr(service, "parse_stage", lambda *_a, **_k: {"parsed": 0})
    monkeypatch.setattr(service, "export_text_stage", lambda *_a, **_k: {"pages": 0})
    result = service.run_processing(_pdf_config(tmp_path), run_ocr=True)
    assert result["ocr"]["failed"] is True
    assert "OCR 崩溃" in result["ocr"]["error"]
    assert result.get("stopped") is True


def test_s234_text_export_failure_is_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from omnicrawler.pdfx import service

    monkeypatch.setattr(service, "ingest", lambda *_a, **_k: {"new": 0})
    monkeypatch.setattr(service, "parse_stage", lambda *_a, **_k: {"parsed": 0})
    monkeypatch.setattr(service, "ocr_stage", lambda *_a, **_k: {"recognized": 0})
    monkeypatch.setattr(service, "export_text_stage", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("导出崩溃")))
    result = service.run_processing(_pdf_config(tmp_path), run_ocr=True)
    assert result["text_export"]["failed"] is True
    assert result.get("stopped") is True


def test_s234_collect_failures_nested() -> None:
    pytest.importorskip("PyQt6")
    from omnicrawler.gui.views.pdf_workbench import _collect_failures

    assert _collect_failures({}) == []
    assert _collect_failures({"status": {"documents": {}}}) == []
    result = {
        "processing": {
            "ingest": {"new": 1},
            "ocr": {"failed": True, "error": "依赖缺失"},
        },
        "extract": {"records": 0},
        "stopped": True,
    }
    failures = _collect_failures(result)
    assert len(failures) == 2
    assert any("依赖缺失" in item for item in failures)
    assert any("已停止" in item for item in failures)


# -- S2.3.5 计数与目录 -------------------------------------------------------


def test_s235_pdf_input_dir_default_and_custom(tmp_path: Path) -> None:
    from omnicrawler.core.config import load_config

    task_path = tmp_path / "task.yaml"
    task_path.write_text(
        f"project: {{name: x, workspace: '{tmp_path / 'ws'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    config = load_config(task_path)
    from omnicrawler.pipeline_ops.pdf_integration import _pdf_input_dir

    assert _pdf_input_dir(config) == (config.workspace / "artifacts" / "pdf").resolve()
    config.raw["storage"] = {"objects": {"local_directory": "pool"}}
    assert _pdf_input_dir(config) == (config.workspace / "pool" / "pdf").resolve()


def test_s235_pdf_scan_recursive(tmp_path: Path) -> None:
    from omnicrawler.pipeline_ops.pdf_integration import _pdf_input_dir

    root = _pdf_input_dir(
        SimpleNamespace(
            workspace=tmp_path,
            section=lambda _name: {"objects": {}},
        )
    )
    (root / "sub").mkdir(parents=True)
    (root / "a.pdf").write_bytes(b"x")
    (root / "sub" / "b.pdf").write_bytes(b"y")
    (root / "sub" / "not.pdf.txt").write_bytes(b"z")
    files = sorted(path.name for path in root.rglob("*.pdf"))
    assert files == ["a.pdf", "b.pdf"]


# -- S2.3.6/7 类型白名单 -----------------------------------------------------


def test_s2367_boolean_entity_relationship_types_accepted(tmp_path: Path) -> None:
    from omnicrawler.pdfx.config import load_config
    from omnicrawler.pdfx.normalization import normalize_value

    config_path = _pdf_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        text.replace(
            "type: amount, source: content, target_unit: 元",
            "type: amount, source: content, target_unit: 元",
        )
        + "  - {name: has_guarantee, label: 是否担保, type: boolean, source: content}\n"
        "  - {name: subject, label: 主体, type: entity, source: content}\n"
        "  - {name: relation, label: 关系, type: relationship, source: content, aliases: [参股]}\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    field_map = config.field_map()
    assert field_map["has_guarantee"].type == "boolean"
    assert field_map["subject"].type == "entity"
    assert field_map["relation"].type == "relationship"

    assert normalize_value("是", field_map["has_guarantee"]) == ("1", None)
    assert normalize_value("否", field_map["has_guarantee"]) == ("0", None)
    assert normalize_value("某某集团", field_map["subject"]) == ("某某集团", None)
    assert normalize_value("参股", field_map["relation"]) == ("参股", None)


# -- S2.3.2 LLM 构造容错 -----------------------------------------------------


def test_s232_llm_client_failure_degrades_to_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from omnicrawler.pdfx import extraction
    from omnicrawler.pdfx.config import load_config
    from omnicrawler.pdfx.database import Database

    config = load_config(_pdf_config(tmp_path))
    db = Database(config.database)
    with db.connection:
        db.execute(
            "INSERT INTO documents(doc_id, sha256, primary_path, filename, size_bytes, status, created_at, updated_at) "
            "VALUES('d1', 'h1', '/p/1.pdf', 'a.pdf', 10, 'parsed', '2024-01-01', '2024-01-01')"
        )
    seen: list[object] = []
    monkeypatch.setattr(
        extraction, "create_llm_client",
        lambda _config: (_ for _ in ()).throw(ValueError("LLM API Key为空")),
    )
    monkeypatch.setattr(
        extraction, "extract_document",
        lambda _config, _db, row, client, resolver: seen.append(client) or 0,
    )
    summary = extraction.extraction_stage(config, db)
    assert summary["selected"] == 1 and summary["documents"] == 1
    assert seen == [None]  # 构造失败 → client=None 纯规则模式继续
    db.close()
