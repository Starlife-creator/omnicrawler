"""Dependency-driven refresh of unreviewed PDF processing results."""
from __future__ import annotations

import logging
from typing import Any

from .config import ProjectConfig
from .database import Database
from .dependencies import ocr_digest, parser_digest
from .parser import PARSER_VERSION
from .utils import utcnow

LOGGER = logging.getLogger(__name__)
_REVIEWED = "human_accepted','human_rejected"


def _event(db: Database, doc_id: str, stage: str, action: str,
           old: str | None, new: str | None) -> None:
    if db.fetchone(
        "SELECT 1 FROM dependency_events WHERE doc_id=? AND stage=? AND action=? "
        "AND old_digest IS ? AND new_digest IS ?", (doc_id, stage, action, old, new),
    ):
        return
    db.execute(
        "INSERT INTO dependency_events(doc_id,stage,action,old_digest,new_digest,created_at) "
        "VALUES(?,?,?,?,?,?)", (doc_id, stage, action, old, new, utcnow()),
    )


def refresh_processing(config: ProjectConfig, db: Database) -> dict[str, Any]:
    """Invalidate only derived data whose completed input identity has changed.

    Existing installations get a baseline for their current pages: their historical
    runtime/model identity cannot be reconstructed reliably after the fact.
    """
    parser = parser_digest(config, PARSER_VERSION)
    ocr = ocr_digest(config)
    summary: dict[str, Any] = {"parse_invalidated": 0, "ocr_invalidated": 0,
                              "protected_reviewed": 0, "baseline_documents": 0,
                              "dependency_notice": ""}
    rows = db.fetchall("SELECT doc_id,status FROM documents ORDER BY doc_id")
    for row in rows:
        doc_id, status = row["doc_id"], row["status"]
        if status in {"ingested", "parse_failed", "parse_dead", "invalid", "needs_password"}:
            continue
        protected = db.fetchone(
            "SELECT 1 FROM records WHERE doc_id=? AND review_status IN ('" + _REVIEWED + "') LIMIT 1",
            (doc_id,),
        ) is not None
        parse_record = db.fetchone(
            "SELECT digest FROM processing_dependencies WHERE doc_id=? AND stage='parse'", (doc_id,))
        ocr_row = db.fetchone(
            "SELECT pd.digest FROM processing_dependencies pd WHERE pd.doc_id=? AND pd.stage='ocr' "
            "AND NOT EXISTS(SELECT 1 FROM pages p WHERE p.doc_id=pd.doc_id "
            "AND p.needs_ocr=1 AND p.ocr_status!='done')", (doc_id,))
        if parse_record is None:
            with db.transaction() as conn:
                db.record_dependency(conn, doc_id, "parse", parser, "baseline")
            summary["baseline_documents"] += 1
        parse_digest = parser if parse_record is None else parse_record["digest"]
        if ocr_row is None and db.fetchone(
            "SELECT 1 FROM pages WHERE doc_id=? AND (needs_ocr=0 OR ocr_status='done') LIMIT 1",
            (doc_id,),
        ):
            with db.transaction() as conn:
                db.record_dependency(conn, doc_id, "ocr", ocr, "baseline")
        parse_changed = parse_digest != parser
        ocr_old_digest = ocr if ocr_row is None else ocr_row["digest"]
        ocr_changed = ocr_old_digest != ocr
        if not parse_changed and not ocr_changed:
            continue
        if protected:
            summary["protected_reviewed"] += 1
            stage = "parse" if parse_changed else "ocr"
            old = parse_digest if parse_changed else ocr_old_digest
            new = parser if parse_changed else ocr
            _event(db, doc_id, stage, "protected_review", old, new)
            continue
        with db.transaction() as conn:
            # Invalidate and audit atomically. Human decisions are checked again
            # after BEGIN IMMEDIATE, so a racing review is never deleted.
            if conn.execute(
                "SELECT 1 FROM records WHERE doc_id=? AND review_status IN ('" + _REVIEWED + "') LIMIT 1",
                (doc_id,),
            ).fetchone():
                continue
            if parse_changed:
                old = parse_digest
                conn.execute("DELETE FROM records WHERE doc_id=?", (doc_id,))
                conn.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
                conn.execute(
                    "UPDATE documents SET status='ingested',document_type=NULL,text_page_count=0,"
                    "ocr_page_count=0,candidate_page_count=0,error=NULL,parser_version=NULL,updated_at=? WHERE doc_id=?",
                    (utcnow(), doc_id),
                )
                conn.execute("DELETE FROM processing_dependencies WHERE doc_id=?", (doc_id,))
                conn.execute(
                    "INSERT INTO dependency_events(doc_id,stage,action,old_digest,new_digest,created_at) "
                    "VALUES(?,'parse','invalidated',?,?,?)", (doc_id, old, parser, utcnow()),
                )
                summary["parse_invalidated"] += 1
            elif ocr_changed:
                assert ocr_old_digest is not None
                old = ocr_old_digest
                conn.execute("DELETE FROM records WHERE doc_id=?", (doc_id,))
                conn.execute(
                    "UPDATE pages SET ocr_text=NULL,final_text=native_text,parse_method='native',"
                    "ocr_status=CASE WHEN needs_ocr=1 THEN 'pending' ELSE 'not_needed' END,"
                    "ocr_confidence=NULL,ocr_structure_json=NULL,candidate_score=0,is_candidate=0,"
                    "evidence_json=NULL,updated_at=? WHERE doc_id=?", (utcnow(), doc_id),
                )
                conn.execute(
                    "UPDATE documents SET status=CASE WHEN EXISTS(SELECT 1 FROM pages WHERE doc_id=? AND needs_ocr=1) "
                    "THEN 'parsed_native' ELSE 'parsed' END,document_type=NULL,ocr_page_count=0,"
                    "candidate_page_count=0,updated_at=? WHERE doc_id=?", (doc_id, utcnow(), doc_id),
                )
                # Keep the last successfully applied OCR identity while pages
                # are pending/failed. Advancing it before successful recognition
                # would silently turn a failed dependency refresh into a baseline.
                conn.execute(
                    "INSERT INTO dependency_events(doc_id,stage,action,old_digest,new_digest,created_at) "
                    "VALUES(?,'ocr','invalidated',?,?,?)", (doc_id, old, ocr, utcnow()),
                )
                summary["ocr_invalidated"] += 1
    if summary["protected_reviewed"]:
        summary["dependency_notice"] = (
            f"{summary['protected_reviewed']} 份含人工决定的文档发现解析/OCR依赖变化；"
            "旧结果已保留，新依赖未自动应用。"
        )
        LOGGER.warning(summary["dependency_notice"])
    return summary
