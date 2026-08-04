from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import ProjectConfig
from .database import Database
from .safe_regex import findall_count
from .utils import utcnow


@dataclass(slots=True)
class CandidatePage:
    page_no: int
    text: str
    score: float
    parse_method: str
    ocr_confidence: float | None


def score_page(text: str, config: ProjectConfig) -> tuple[float, dict[str, Any]]:
    # D11：入库文本保留原始空白（列对齐信号），检索匹配前统一压缩
    folded = re.sub(r"\s+", " ", text.casefold())
    score = 0.0
    hits: dict[str, Any] = {"terms": {}, "patterns": {}}
    alias_weight = float(config.retrieval.get("alias_weight", 1.0))
    pattern_weight = float(config.retrieval.get("pattern_weight", 2.0))
    for spec in config.fields:
        if spec.source not in {"content", "both"}:
            continue
        for term in spec.search_terms:
            count = folded.count(term.casefold())
            if count:
                contribution = alias_weight * min(count, 5)
                score += contribution
                hits["terms"][term] = count
        for pattern in spec.patterns:
            try:
                count = findall_count(pattern, text)
            except (re.error, ValueError):
                count = 0
            if count:
                score += pattern_weight * min(count, 3)
                hits["patterns"][spec.name] = count
    for negative in config.retrieval.get("negative_keywords", []):
        if str(negative).casefold() in folded:
            score -= float(config.retrieval.get("negative_weight", 1.0))
    return max(0.0, score), hits


def select_candidates(config: ProjectConfig, db: Database, doc_id: str) -> list[CandidatePage]:
    rows = db.fetchall(
        "SELECT page_no, final_text, parse_method, ocr_confidence FROM pages WHERE doc_id=? ORDER BY page_no",
        (doc_id,),
    )
    if not rows:
        return []
    scored: list[tuple[int, float, dict[str, Any], Any]] = []
    for row in rows:
        score, evidence = score_page(row["final_text"] or "", config)
        scored.append((int(row["page_no"]), score, evidence, row))

    top_n = int(config.retrieval.get("top_pages", 3))
    min_score = float(config.retrieval.get("min_score", 1.0))
    neighbors = int(config.retrieval.get("neighbor_pages", 1))
    ranked = sorted((item for item in scored if item[1] >= min_score), key=lambda x: (-x[1], x[0]))
    selected_numbers = {item[0] for item in ranked[:top_n]}
    for page_no in list(selected_numbers):
        for offset in range(1, neighbors + 1):
            if page_no - offset >= 1:
                selected_numbers.add(page_no - offset)
            if page_no + offset <= len(rows):
                selected_numbers.add(page_no + offset)
    if not selected_numbers:
        fallback = config.retrieval.get("fallback_pages", [1])
        # D21：按实际页号集合过滤，页号不连续时不再 KeyError
        by_number = {int(item[3]["page_no"]): item for item in scored}
        selected_numbers = {int(page) for page in fallback if int(page) in by_number}
    else:
        by_number = {int(item[3]["page_no"]): item for item in scored}
    now = utcnow()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE pages SET candidate_score=0, is_candidate=0, evidence_json=NULL, updated_at=? WHERE doc_id=?",
            (now, doc_id),
        )
        for page_no, score, evidence, _row in scored:
            import json
            conn.execute(
                "UPDATE pages SET candidate_score=?, is_candidate=?, evidence_json=?, updated_at=? WHERE doc_id=? AND page_no=?",
                (score, int(page_no in selected_numbers), json.dumps(evidence, ensure_ascii=False), now, doc_id, page_no),
            )
        conn.execute(
            "UPDATE documents SET candidate_page_count=?, updated_at=? WHERE doc_id=?",
            (len(selected_numbers), now, doc_id),
        )

    result: list[CandidatePage] = []
    for page_no in sorted(selected_numbers):
        _, score, _, row = by_number[page_no]
        result.append(CandidatePage(
            page_no=page_no,
            text=row["final_text"] or "",
            score=score,
            parse_method=row["parse_method"] or "unknown",
            ocr_confidence=row["ocr_confidence"],
        ))
    return result

