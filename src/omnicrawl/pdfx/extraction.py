from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .config import FieldSpec, ProjectConfig
from .database import Database
from .llm import build_user_content, create_llm_client
from .normalization import EntityResolver, normalize_value
from .retrieval import CandidatePage, select_candidates
from .safe_regex import search as safe_regex_search
from .utils import utcnow
from .validation import validate_record


def _evidence_window(text: str, start: int, end: int, radius: int = 100) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right].replace("\n", " ").strip()


def rule_extract_field(
    spec: FieldSpec,
    filename: str,
    pages: list[CandidatePage],
) -> dict[str, Any] | None:
    sources: list[tuple[int | None, str, str]] = []
    if spec.source in {"filename", "both"}:
        sources.append((None, filename, "filename_rule"))
    if spec.source in {"content", "both"}:
        sources.extend((page.page_no, page.text, "content_rule") for page in pages)

    for page_no, text, method in sources:
        for pattern in spec.patterns:
            try:
                match = safe_regex_search(pattern, text)
            except (re.error, ValueError) as exc:
                raise ValueError(f"字段 {spec.name} 的正则表达式错误: {pattern}: {exc}") from exc
            if match:
                if match.groupdict().get("value") is not None:
                    raw = match.group("value")
                elif match.lastindex:
                    raw = match.group(1)
                else:
                    raw = match.group(0)
                return {
                    "raw_value": raw.strip(),
                    "page_no": page_no,
                    "evidence": _evidence_window(text, match.start(), match.end()),
                    "extraction_method": method,
                }
        for alias in spec.search_terms:
            pattern = re.compile(
                rf"{re.escape(alias)}\s*[：:]?\s*(?P<value>[^\n；;]{{1,100}})", re.I
            )
            match = pattern.search(text)
            if match:
                return {
                    "raw_value": match.group("value").strip(" ：:"),
                    "page_no": page_no,
                    "evidence": _evidence_window(text, match.start(), match.end()),
                    "extraction_method": method,
                }
    return None


def rule_extract(config: ProjectConfig, filename: str, pages: list[CandidatePage]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for spec in config.fields:
        value = rule_extract_field(spec, filename, pages)
        if value:
            result[spec.name] = value
    return result


def _normalize_llm_records(payload: dict[str, Any]) -> list[dict[str, dict[str, Any]]]:
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("LLM结果中的 records 必须是数组")
    normalized: list[dict[str, dict[str, Any]]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        fields = record.get("fields", {})
        if not isinstance(fields, dict):
            continue
        cleaned: dict[str, dict[str, Any]] = {}
        for name, value in fields.items():
            if value is None:
                continue
            if isinstance(value, str):
                value = {"raw_value": value, "page_no": None, "evidence": None}
            if not isinstance(value, dict):
                continue
            raw = value.get("raw_value")
            if raw is None or not str(raw).strip():
                continue
            cleaned[name] = {
                "raw_value": str(raw).strip(),
                "page_no": value.get("page_no"),
                "evidence": value.get("evidence"),
                "extraction_method": "llm",
            }
        if cleaned:
            normalized.append(cleaned)
    return normalized


def _observable_confidence(value: dict[str, Any], pages_by_no: dict[int, CandidatePage]) -> float:
    method = value.get("extraction_method")
    if method in {"filename_rule", "content_rule"}:
        return 0.98
    page_no = value.get("page_no")
    evidence = str(value.get("evidence") or "").strip()
    raw = str(value.get("raw_value") or "").strip()
    page = pages_by_no.get(int(page_no)) if str(page_no).isdigit() else None
    if not page:
        return 0.45
    supported = bool(raw and raw in page.text) or bool(evidence and evidence in page.text)
    if not supported:
        return 0.55
    if page.parse_method == "ocr":
        quality = page.ocr_confidence if page.ocr_confidence is not None else 0.75
        return min(0.90, 0.65 + 0.25 * quality)
    return 0.92


def _merge_rules(
    records: list[dict[str, dict[str, Any]]],
    rules: dict[str, dict[str, Any]],
) -> list[dict[str, dict[str, Any]]]:
    if not records:
        return [dict(rules)] if rules else []
    for record in records:
        for name, value in rules.items():
            record.setdefault(name, value)
    return records


def extract_document(
    config: ProjectConfig,
    db: Database,
    doc_row,
    llm_client,
    entity_resolver: EntityResolver,
) -> int:
    doc_id = doc_row["doc_id"]
    filename = doc_row["filename"]
    pages = select_candidates(config, db, doc_id)
    rules = rule_extract(config, filename, pages)
    payload: dict[str, Any] = {"document_type": None, "records": []}
    if llm_client is not None and pages:
        user_content = build_user_content(
            config, filename, pages,
            pdf_path=doc_row["primary_path"] if config.llm.get("include_page_images") else None,
        )
        payload = llm_client.extract(user_content)
        records = _normalize_llm_records(payload)
        method = "hybrid"
    else:
        records = []
        method = "rules"
    records = _merge_rules(records, rules)

    pages_by_no = {page.page_no: page for page in pages}
    field_map = config.field_map()
    now = utcnow()
    with db.transaction() as conn:
        conn.execute("DELETE FROM records WHERE doc_id=?", (doc_id,))
        for index, record in enumerate(records, start=1):
            values: dict[str, dict[str, Any]] = {}
            confidences: list[float] = []
            for name, spec in field_map.items():
                value = record.get(name, {})
                raw_value = value.get("raw_value")
                normalized, unit = normalize_value(raw_value, spec, entity_resolver)
                confidence = _observable_confidence(value, pages_by_no) if raw_value else 0.0
                if raw_value:
                    confidences.append(confidence)
                values[name] = {
                    **value,
                    "raw_value": raw_value,
                    "normalized_value": normalized,
                    "unit": unit,
                    "confidence": confidence,
                }
            record_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            validation = validate_record(config, values, record_confidence)
            record_id = hashlib.sha256(f"{doc_id}:{index}".encode()).hexdigest()
            conn.execute(
                """
                INSERT INTO records(
                    record_id, doc_id, record_index, extraction_method, confidence,
                    review_status, validation_status, validation_messages, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id, doc_id, index, method, record_confidence,
                    validation.review_status, validation.status,
                    json.dumps(validation.messages, ensure_ascii=False), now, now,
                ),
            )
            for name, value in values.items():
                conn.execute(
                    """
                    INSERT INTO field_values(
                        record_id, field_name, raw_value, normalized_value, unit,
                        page_no, evidence, extraction_method, confidence, validation_status
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        record_id, name, value.get("raw_value"), value.get("normalized_value"),
                        value.get("unit"), value.get("page_no"), value.get("evidence"),
                        value.get("extraction_method"), value.get("confidence"), validation.status,
                    ),
                )
        conn.execute(
            "UPDATE documents SET status=?, document_type=?, error=NULL, updated_at=? WHERE doc_id=?",
            (
                "extracted" if records else "extracted_no_data",
                payload.get("document_type"), now, doc_id,
            ),
        )
    return len(records)


def extraction_stage(
    config: ProjectConfig,
    db: Database,
    limit: int | None = None,
    workers: int | None = None,
) -> dict[str, int]:
    if limit is not None and limit < 0:
        raise ValueError("limit 不能为负数")
    rows = db.fetchall(
        """
        SELECT doc_id, filename, primary_path FROM documents
        WHERE status IN ('parsed','parsed_partial','parsed_native','extract_failed')
        ORDER BY filename
        """
    )
    if limit is not None:
        rows = rows[:limit]
    workers = int(config.extraction.get("workers", 4)) if workers is None else workers
    if not 1 <= workers <= 64:
        raise ValueError("workers 必须在1到64之间")
    summary = {"selected": len(rows), "documents": 0, "records": 0, "no_data": 0, "failed": 0}
    if not rows:
        return summary
    client = create_llm_client(config)
    resolver = EntityResolver.from_config(config)

    def work(row):
        # One connection per worker; WAL and busy_timeout serialize short writes while
        # network inference remains concurrent.
        with Database(config.database) as worker_db:
            return extract_document(config, worker_db, row, client, resolver)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, row): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                count = future.result()
                summary["documents"] += 1
                summary["records"] += count
                if count == 0:
                    summary["no_data"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["failed"] += 1
                db.add_error(row["doc_id"], "extract", exc)
                db.execute(
                    "UPDATE documents SET status='extract_failed', error=?, updated_at=? WHERE doc_id=?",
                    (str(exc)[:4000], utcnow(), row["doc_id"]),
                )
    return summary
