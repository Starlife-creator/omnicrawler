"""Offline processor fixture checks, scoped history and recommendation quarantine."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from ..core.runtime_paths import portable_data_root
from .evidence import template_binding
from .parameters import validate_parameters


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def parameters_for(record: Any, url: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    supplied = {"seed_url": url} if "seed_url" in record.metadata.placeholders else {}
    supplied.update(values or {})
    return validate_parameters(record.metadata.placeholders, supplied, strict=False)


class VerificationStore:
    def __init__(self, path: Path | None = None):
        configured = os.environ.get("OMNICRAWL_TEMPLATE_CHECKS_PATH")
        self.path = path or (Path(configured) if configured else portable_data_root() / ".omnicrawler/template_checks.sqlite3")

    def record(self, report: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise ValueError("模板复验历史版本过高，未修改历史")
            connection.execute("CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY,template_id TEXT NOT NULL,"
                               "template_sha TEXT NOT NULL,url_sha TEXT NOT NULL,parameters_sha TEXT NOT NULL,"
                               "report_json TEXT NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS checks_scope ON checks(template_id,template_sha,url_sha,parameters_sha,id)")
            connection.execute("INSERT INTO checks(template_id,template_sha,url_sha,parameters_sha,report_json) VALUES(?,?,?,?,?)",
                               (report["template_id"], report["template_sha256"], report["url_sha256"],
                                report["parameters_sha256"], json.dumps(report, ensure_ascii=False)))
            connection.execute("PRAGMA user_version=1")

    def history(self, template_id: str) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("模板复验历史版本不受支持")
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT report_json FROM checks WHERE template_id=? ORDER BY id DESC LIMIT 100", (template_id,))]

    def latest(self, record: Any, url: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        binding = template_binding(record, parameters_for(record, url))
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("模板复验历史版本不受支持")
            row = connection.execute("SELECT report_json FROM checks WHERE template_id=? AND template_sha=? "
                                     "AND url_sha=? AND parameters_sha=? ORDER BY id DESC LIMIT 1",
                                     (record.metadata.template_id, binding["template_sha256"],
                                      hashlib.sha256(url.encode()).hexdigest(), binding["parameters_sha256"])).fetchone()
            return json.loads(row[0]) if row else None

