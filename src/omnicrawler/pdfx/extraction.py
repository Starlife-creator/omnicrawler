from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .concurrency import iter_bounded_futures
from .config import FieldSpec, ProjectConfig
from .database import Database
from .llm import build_user_content, create_llm_client
from .normalization import EntityResolver, normalize_value
from .retrieval import CandidatePage, select_candidates
from .safe_regex import search as safe_regex_search
from .utils import utcnow
from .validation import validate_record

LOGGER = logging.getLogger(__name__)


def _collapse_ws(value: str) -> str:
    """空白归一（D34：证据比对双方统一压缩空白，避免模型原文抄写带空格误判）。"""
    return re.sub(r"\s+", " ", str(value)).strip()


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
                    "matched_by_pattern": True,  # D22：显式 patterns 命中 → 高置信
                }
        for alias in spec.search_terms:
            # D23：alias 兜底必须存在分隔符（：:），且按字段类型校验值形态
            alias_pattern = re.compile(
                rf"{re.escape(alias)}\s*[：:]\s*(?P<value>[^\n；;]{{1,100}})", re.I
            )
            match = alias_pattern.search(text)
            if match:
                candidate = match.group("value").strip(" ：:")
                if not candidate or not _shape_plausible(candidate, spec.type):
                    continue
                return {
                    "raw_value": candidate,
                    "page_no": page_no,
                    "evidence": _evidence_window(text, match.start(), match.end()),
                    "extraction_method": method,
                    "matched_by_pattern": False,  # D22：alias 宽松兜底 → 低置信
                }
    return None


def _shape_plausible(value: str, spec_type: str) -> bool:
    """D23：按字段类型预校验 alias 兜底值形态，拒绝"本次担保金额尚需股东大会审议"类误抓。"""
    kind = str(spec_type).casefold()
    if kind in {"amount", "currency", "number", "integer", "percent", "year"}:
        return any(ch.isdigit() for ch in value)
    if kind == "date":
        return any(ch.isdigit() for ch in value) and any(ch in value for ch in "年月-./")
    if kind in {"enum", "relationship", "code"}:
        return bool(value.strip())
    return bool(value.strip())


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
    """D22：规则也分级——显式 patterns 命中高置信；alias 宽松兜底低置信且要求证据可验证。"""
    method = value.get("extraction_method")
    raw = str(value.get("raw_value") or "").strip()
    evidence = str(value.get("evidence") or "").strip()
    page_no = value.get("page_no")
    page_no_text = str(page_no) if page_no is not None else ""
    page = pages_by_no.get(int(page_no_text)) if page_no_text.isdigit() else None
    if method in {"filename_rule", "content_rule"}:
        if value.get("matched_by_pattern"):
            return 0.98
        # D22：alias 宽松兜底一律低置信（<0.6），进复核
        return 0.55
    if not page:
        return 0.45
    # D34：证据比对统一空白归一
    supported = bool(
        raw and _collapse_ws(raw) in _collapse_ws(page.text)
    ) or bool(evidence and _collapse_ws(evidence) in _collapse_ws(page.text))
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
    """把规则结果并入 LLM 记录。

    D31：多记录场景不回填 content 类规则值（避免多条记录共享同一值严重失真）；
    filename_rule 是文件名级信息，回填并标记 shared_from_rules 便于复核。
    """
    if not records:
        return [copy.deepcopy(rules)] if rules else []
    for record in records:
        for name, value in rules.items():
            if name in record:
                continue
            if len(records) == 1 or value.get("extraction_method") == "filename_rule":
                record[name] = {**value, "shared_from_rules": True}
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
    # D19：含人工复核记录的文档不自动重抽（record_id 确定性生成会与保留记录冲突；
    # 人工修正值优先，需用户手动清除复核状态后才会被新抽取覆盖）
    reviewed = db.fetchone(
        "SELECT COUNT(*) AS n FROM records WHERE doc_id=? AND review_status='human_accepted'",
        (doc_id,),
    )
    if reviewed and reviewed["n"]:
        return 0
    pages = select_candidates(config, db, doc_id)
    rules = rule_extract(config, filename, pages)
    payload: dict[str, Any] = {"document_type": None, "records": []}
    if llm_client is not None and pages:
        user_content = build_user_content(
            config, filename, pages,
            pdf_path=doc_row["primary_path"] if config.llm.get("include_page_images") else None,
        )
        try:
            payload = llm_client.extract(user_content)
            records = _normalize_llm_records(payload)
            method = "hybrid"
        except Exception as exc:  # noqa: BLE001
            # D17：LLM 偶发失败不丢弃已抽取的规则结果——降级写规则并标注 rules_fallback
            LOGGER.warning("LLM 抽取失败，降级为规则结果: %s", exc)
            records = []
            payload = {"document_type": None, "records": []}
            method = "rules_fallback"
    else:
        records = []
        method = "rules"
    records = _merge_rules(records, rules)

    pages_by_no = {page.page_no: page for page in pages}
    field_map = config.field_map()
    now = utcnow()
    with db.transaction() as conn:
        # D19：不覆盖人工复核过的记录（human_accepted 保留，下次续跑/重抽不静默清空人工修正值）
        conn.execute(
            "DELETE FROM records WHERE doc_id=? AND review_status != 'human_accepted'",
            (doc_id,),
        )
        for index, record in enumerate(records, start=1):
            values: dict[str, dict[str, Any]] = {}
            confidences: list[float] = []
            required_names = {spec.name for spec in field_map.values() if spec.required}
            for name, spec in field_map.items():
                value = record.get(name, {})
                raw_value = value.get("raw_value")
                normalized, unit = normalize_value(raw_value, spec, entity_resolver)
                confidence = _observable_confidence(value, pages_by_no) if raw_value else 0.0
                # D24：置信度分母含全部必填字段，缺失按 0 计（6 字段只抽 1 个不能仍是高置信）
                if raw_value or name in required_names:
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
    should_stop: Callable[[], bool] | None = None,
    on_document: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    if limit is not None and limit < 0:
        raise ValueError("limit 不能为负数")
    select_sql = """
        SELECT doc_id, filename, primary_path FROM documents
        WHERE status IN ('parsed','parsed_partial','parsed_native','extract_failed')
        ORDER BY filename, doc_id
        """
    select_params: tuple[Any, ...] = ()
    if hasattr(db, "iter_rows"):
        total_row = db.fetchone(
            "SELECT COUNT(*) AS n FROM documents "
            "WHERE status IN ('parsed','parsed_partial','parsed_native','extract_failed')"
        )
        selected = int(total_row["n"] if total_row else 0)
        if limit is not None:
            selected = min(selected, limit)
            select_sql += " LIMIT ?"
            select_params = (limit,)
        rows = db.iter_rows(select_sql, select_params)
    else:  # Lightweight test doubles and third-party Database adapters.
        buffered_rows = db.fetchall(select_sql, select_params)
        if limit is not None:
            buffered_rows = buffered_rows[:limit]
        selected = len(buffered_rows)
        rows = iter(buffered_rows)
    workers = int(config.extraction.get("workers", 4)) if workers is None else workers
    if not 1 <= workers <= 64:
        raise ValueError("workers 必须在1到64之间")
    summary: dict[str, Any] = {
        "selected": selected, "documents": 0, "records": 0, "no_data": 0, "failed": 0,
    }
    if not selected:
        return summary
    # S2.3.2：LLM 客户端构造失败（Key 空/参数非法/依赖缺失）降级为纯规则模式，不中断抽取
    try:
        client = create_llm_client(config)
    except Exception as exc:  # noqa: BLE001 - missing/empty LLM config must not break extraction
        LOGGER.warning("LLM 客户端构造失败，降级为纯规则抽取: %s", exc)
        client = None
    resolver = EntityResolver.from_config(config)

    # D40：每线程复用数据库连接（十万文档不再十万次建连+建表脚本）；
    # D41 的 BEGIN IMMEDIATE + busy_timeout 保证线程并发写不冲突
    _thread_db = threading.local()
    _thread_connections: list[Database] = []

    def work(row):
        worker_db = getattr(_thread_db, "db", None)
        if worker_db is None:
            worker_db = Database(config.database)
            _thread_db.db = worker_db
            _thread_connections.append(worker_db)  # list.append 在 GIL 下线程安全
        return extract_document(config, worker_db, row, client, resolver)

    def mark_stopped() -> None:
        summary["stopped"] = True

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            completed = iter_bounded_futures(
                rows,
                lambda row: pool.submit(work, row),
                max_in_flight=max(1, workers * 4),
                should_stop=should_stop,
                on_stop=mark_stopped,
            )
            for future, row in completed:
                try:
                    count = future.result()
                    summary["documents"] += 1
                    summary["records"] += count
                    if on_document is not None:
                        # B12：实时汇报已处理文档数，避免大批量时进度条长时间不动误以为卡死
                        on_document(summary["documents"], selected)
                    if count == 0:
                        summary["no_data"] += 1
                except Exception as exc:  # noqa: BLE001
                    summary["failed"] += 1
                    db.add_error(row["doc_id"], "extract", exc)
                    db.execute(
                        "UPDATE documents SET status='extract_failed', error=?, updated_at=? WHERE doc_id=?",
                        (str(exc)[:4000], utcnow(), row["doc_id"]),
                    )
    finally:
        # executor 退出（在途窗口排空）后关闭线程连接，避免 sqlite 文件锁残留
        for conn in _thread_connections:
            try:
                conn.close()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("关闭抽取线程数据库连接失败: %s", exc)
    # C49/D2：抽取方式分布（rules / hybrid / rules_fallback），供 GUI 明示"是否真的用了大模型"
    summary["extraction_methods"] = {
        row["method"]: row["n"]
        for row in db.fetchall(
            "SELECT extraction_method AS method, COUNT(*) AS n FROM records GROUP BY extraction_method"
        )
    }
    return summary
