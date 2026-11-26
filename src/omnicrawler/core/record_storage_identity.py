"""One record ID definition for persistence and provenance."""
from __future__ import annotations

import hashlib
import uuid

from .models import CrawlRequest, ExtractedRecord
from .utils import json_text


def storage_identity(run_id: str, request: CrawlRequest, record: ExtractedRecord,
                     index: int, deduplicate_by: tuple[str, ...] = ()) -> tuple[str, bool]:
    identity = [record.data.get(name) for name in deduplicate_by]
    stable = bool(identity) and all(value not in (None, "", []) for value in identity)
    # 2026-11-26: 键里必须带 record_type。
    #
    # 实测（写入 30 条列表 + 1 条单品）：两条记录的 record_id **完全相同**，
    # 后写的把先写的挤掉 —— DB 里只剩 29 + 1，**第 1 条列表记录静默消失**。
    #
    # 根因是两条路径不一致：stable 分支早就带了 record_type，
    # 非 stable 分支漏了。于是「同一页产出两种记录类型」时必然相撞。
    # 这不是字段问题、也不是 JSON-LD 问题 —— 任何多粒度产出都会踩到。
    key = f"{run_id}:{request.fingerprint}:{record.record_type}:{index}"
    if stable:
        digest = hashlib.sha256(json_text(record.data).encode("utf-8")).hexdigest()
        key = f"{run_id}:entity:{record.record_type}:{json_text(identity)}:{digest}"
    return uuid.uuid5(uuid.NAMESPACE_URL, key).hex, stable
