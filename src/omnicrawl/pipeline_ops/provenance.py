"""采集产物与文档处理核心之间的来源协议。

这个模块只输出通用 JSONL，PDF 核心不需要了解采集器的数据库结构。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.safe_data import safe_json_loads
from ..core.utils import atomic_write, utcnow
from ..state import StateStore


def write_pdf_source_manifest(
    workspace: Path,
    state: StateStore,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """将已下载 PDF 的 URL、父页、批次和哈希写入来源清单。"""
    pdf_dir = workspace / "artifacts" / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    rows = state.rows(
        """
        SELECT a.run_id, a.request_fingerprint, a.source_url, a.local_path,
               a.content_type, a.size_bytes, a.sha256, a.created_at,
               f.parent_url, f.meta_json
        FROM artifacts AS a
        LEFT JOIN frontier AS f ON f.fingerprint = a.request_fingerprint
        WHERE lower(a.local_path) LIKE '%.pdf'
        ORDER BY a.created_at, a.id
        """
    )
    items: list[dict[str, Any]] = []
    missing = 0
    for row in rows:
        path = Path(str(row["local_path"])).expanduser().resolve()
        if not path.is_file():
            missing += 1
            continue
        request_meta = safe_json_loads(row.get("meta_json") or "{}", default={})
        item = {
            "filename": path.name,
            "file_path": str(path),
            "source_url": row["source_url"],
            "parent_url": row.get("parent_url"),
            "crawl_run_id": row["run_id"],
            "current_run": bool(run_id and row["run_id"] == run_id),
            "request_fingerprint": row["request_fingerprint"],
            "content_type": row["content_type"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "downloaded_at": row["created_at"],
            "request_meta": request_meta,
        }
        items.append(item)
    payload = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        for item in items
    ).encode("utf-8")
    manifest = pdf_dir / "source_manifest.jsonl"
    atomic_write(manifest, payload)
    return {
        "path": str(manifest),
        "documents": len(items),
        "missing_files": missing,
        "generated_at": utcnow(),
    }
