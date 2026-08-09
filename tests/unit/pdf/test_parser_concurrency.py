"""S1.5.1 消费方测试：parse_stage 每线程独立 DB 连接 + 短事务批写。

验证多 worker 并发解析不再触发 SQLITE_BUSY / "cannot start a transaction
within a transaction"，且逐文档数据完整落库。
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import fitz

from omnicrawl.pdfx.config import ProjectConfig
from omnicrawl.pdfx.database import Database
from omnicrawl.pdfx.parser import MAX_PARSE_ATTEMPTS, parse_stage


def _make_pdf(path: Path, pages: int = 3) -> None:
    document = fitz.open()
    for i in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"第{i + 1}页 金额：{i + 1}00万元", fontname="china-s", fontsize=12)
    document.save(path)
    document.close()


def _project(database: Path) -> ProjectConfig:
    return ProjectConfig(
        path=Path("x.yaml"), project_name="t", input_dir=Path("in"), work_dir=Path("work"),
        output_dir=Path("out"), database=database, parser={"workers": 4}, ocr={}, retrieval={},
        llm={}, extraction={}, normalization={}, validation={"auto_accept_confidence": 0.9},
        fields=[],
    )


def _seed(db: Database, pdf_path: Path, doc_id: str) -> None:
    db.execute(
        """
        INSERT INTO documents(doc_id, sha256, primary_path, filename, size_bytes,
            status, attempt_count, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, 'ingested', 0, 't', 't')
        """,
        (doc_id, f"sha-{doc_id}", str(pdf_path), pdf_path.name, pdf_path.stat().st_size),
    )


def test_s151_concurrent_parse_no_busy_or_nested_transaction() -> None:
    """S1.5.1：多 worker 并发解析+写库不报 BUSY/嵌套事务，数据完整落库。"""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db_path = root / "db.sqlite"
        db = Database(db_path)
        try:
            pdfs = []
            for i in range(3):
                pdf = root / f"doc{i}.pdf"
                _make_pdf(pdf, pages=2 + i)
                pdfs.append(pdf)
                _seed(db, pdf, f"doc{i}")

            summary = parse_stage(_project(db_path), db, workers=4)
            assert summary["selected"] == 3
            assert summary["parsed"] == 3
            assert summary["failed"] == 0

            for i in range(3):
                rows = db.fetchall(
                    "SELECT page_no, final_text FROM pages WHERE doc_id=? ORDER BY page_no",
                    (f"doc{i}",),
                )
                assert len(rows) == 2 + i, f"doc{i} 页数不完整"
                assert "金额" in rows[0]["final_text"]

            dead = db.fetchall("SELECT status FROM documents WHERE status='parse_dead'")
            assert not dead, "无文档应被误判为 parse_dead"
        finally:
            db.close()


def test_s151_parse_failure_increments_attempt_and_bounds() -> None:
    """S1.5.1/S1.5.3：损坏 PDF 失败计数累加，不越 MAX_PARSE_ATTEMPTS 上限。"""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db_path = root / "db.sqlite"
        db = Database(db_path)
        try:
            broken = root / "broken.pdf"
            broken.write_bytes(b"%PDF-1.4 not a real pdf body")
            _seed(db, broken, "broken")

            # 一次失败 → parse_failed，attempt_count 1
            summary = parse_stage(_project(db_path), db, workers=1)
            assert summary["failed"] == 1
            row = db.fetchone("SELECT status, attempt_count FROM documents WHERE doc_id='broken'")
            assert row["status"] == "parse_failed"
            assert row["attempt_count"] == 1

            # 连续失败到阈值 → parse_dead
            for _ in range(MAX_PARSE_ATTEMPTS):
                parse_stage(_project(db_path), db, workers=1)
            row = db.fetchone("SELECT status, attempt_count FROM documents WHERE doc_id='broken'")
            assert row["status"] == "parse_dead"
            assert row["attempt_count"] >= MAX_PARSE_ATTEMPTS

            # parse_dead 不再被拉起
            summary = parse_stage(_project(db_path), db, workers=1)
            assert summary["selected"] == 0
        finally:
            db.close()
            # PyMuPDF 对损坏 PDF 的异常路径产生引用环，显式 GC 释放文件句柄（Windows）
            gc.collect()
