from __future__ import annotations

import json
from pathlib import Path

import fitz

from .config import ProjectConfig
from .database import Database
from .utils import iter_pdfs, sha256_file, stable_json, utcnow

MANIFEST_NAMES = (
    "source_manifest.jsonl",
    "download_manifest.jsonl",
    "downloads_manifest.jsonl",
)


def load_source_manifest(input_dir: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Load optional crawler provenance without coupling to a specific crawler."""
    by_path: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for name in MANIFEST_NAMES:
        manifest_path = input_dir / name
        if not manifest_path.exists():
            continue
        with manifest_path.open("r", encoding="utf-8-sig") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"来源清单 {manifest_path} 第 {line_no} 行不是有效 JSON"
                    ) from exc
                if not isinstance(item, dict):
                    continue
                raw_path = (
                    item.get("file_path")
                    or item.get("local_path")
                    or item.get("path")
                    or item.get("filename")
                    or ""
                )
                if raw_path:
                    candidate = Path(str(raw_path)).expanduser()
                    if not candidate.is_absolute():
                        candidate = input_dir / candidate
                    resolved = candidate.resolve()
                    # 安全防御：拒绝清单中指向 input_dir 外部的路径
                    allowed_root = input_dir.resolve()
                    if allowed_root not in resolved.parents and resolved != allowed_root:
                        continue
                    by_path[str(resolved)] = item
                    by_name[candidate.name] = item
    return by_path, by_name


def inspect_pdf(path: Path) -> tuple[int | None, bool, str | None]:
    try:
        with fitz.open(path) as document:
            encrypted = bool(document.needs_pass)
            pages = document.page_count if not encrypted else None
            return pages, encrypted, None
    except Exception as exc:  # noqa: BLE001 - invalid PDFs must be catalogued
        return None, False, f"{type(exc).__name__}: {exc}"


def ingest(config: ProjectConfig, db: Database, limit: int | None = None) -> dict[str, int]:
    if limit is not None and limit < 0:
        raise ValueError("limit 不能为负数")
    if not config.input_dir.exists():
        raise FileNotFoundError(f"PDF目录不存在: {config.input_dir}")
    manifest_by_path, manifest_by_name = load_source_manifest(config.input_dir)
    summary = {"found": 0, "new": 0, "duplicate": 0, "invalid": 0}
    for index, path in enumerate(iter_pdfs(config.input_dir)):
        if limit is not None and index >= limit:
            break
        summary["found"] += 1
        source_meta = manifest_by_path.get(str(path)) or manifest_by_name.get(path.name) or {}
        source_url = str(
            source_meta.get("source_url")
            or source_meta.get("pdf_url")
            or source_meta.get("url")
            or ""
        ).strip() or None
        source_meta_json = stable_json(source_meta) if source_meta else None
        stat = path.stat()
        # S4.4 ④：path+size 命中不再直接跳过——补 SHA-256 校验，
        # 同路径同大小的新文件（内容已变）被正确检出（F699）。
        # 命中项才需哈希（十万份续跑未命中项仍不读盘）。
        existing = db.fetchone(
            "SELECT doc_id, sha256 FROM documents WHERE primary_path=? AND size_bytes=?",
            (str(path), stat.st_size),
        )
        if existing and existing["sha256"]:
            current_digest = sha256_file(path)
            if current_digest == existing["sha256"]:
                db.execute(
                    """INSERT INTO document_sources(
                        doc_id, source_path, source_url, source_meta_json, created_at
                    ) VALUES(?,?,?,?,?)
                    ON CONFLICT(source_path) DO UPDATE SET
                        source_url=COALESCE(excluded.source_url, document_sources.source_url),
                        source_meta_json=COALESCE(
                            excluded.source_meta_json, document_sources.source_meta_json
                        )""",
                    (existing["doc_id"], str(path), source_url, source_meta_json, utcnow()),
                )
                summary["duplicate"] += 1
                continue
            # 内容变化：用当前哈希走"新文档"分支重建
            digest = current_digest
        else:
            digest = sha256_file(path)
        existing = db.fetchone("SELECT doc_id FROM documents WHERE sha256=?", (digest,))
        if existing:
            db.execute(
                """INSERT INTO document_sources(
                    doc_id, source_path, source_url, source_meta_json, created_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(source_path) DO UPDATE SET
                    source_url=COALESCE(excluded.source_url, document_sources.source_url),
                    source_meta_json=COALESCE(
                        excluded.source_meta_json, document_sources.source_meta_json
                    )""",
                (existing["doc_id"], str(path), source_url, source_meta_json, utcnow()),
            )
            summary["duplicate"] += 1
            continue

        page_count, encrypted, error = inspect_pdf(path)
        status = "invalid" if error else ("needs_password" if encrypted else "ingested")
        now = utcnow()
        with db.transaction() as conn:
            # S4.4 ④：内容变化（同路径同大小新文件）时移除旧文档行，
            # 避免 sha256 UNIQUE 与 source_path 关联冲突
            conn.execute("DELETE FROM documents WHERE primary_path=?", (str(path),))
            conn.execute(
                """
                INSERT INTO documents(
                    doc_id, sha256, primary_path, filename, size_bytes, page_count,
                    is_encrypted, status, error, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    digest, digest, str(path), path.name, path.stat().st_size,
                    page_count, int(encrypted), status, error, now, now,
                ),
            )
            conn.execute(
                """INSERT INTO document_sources(
                    doc_id, source_path, source_url, source_meta_json, created_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(source_path) DO UPDATE SET
                    doc_id=excluded.doc_id,
                    source_url=COALESCE(excluded.source_url, document_sources.source_url),
                    source_meta_json=COALESCE(
                        excluded.source_meta_json, document_sources.source_meta_json
                    )""",
                (digest, str(path), source_url, source_meta_json, now),
            )
        if error:
            summary["invalid"] += 1
        else:
            summary["new"] += 1
    return summary
