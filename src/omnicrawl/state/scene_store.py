"""场景/槽位/基因持久化（批 C，C-1 + C-2）。

单库四表（SQLite，全部 SQLite 方言）：
- ``slot_definitions``      槽位定义（scene 下唯一 slot_key）
- ``document_fingerprints`` 文档内容指纹（sha256 去重，避免重复抽取）
- ``extraction_candidates`` 抽取候选槽位值（value_json / confidence / 验收标记）
- ``selector_genes``        选择器基因（场景×槽位下唯一 selector，hits/misses/fitness）

设计决策（批 C）：
- **bundled YAML 只读默认 + DB 单一真源**：scenes/*.yaml 仅在初始化时
  幂等导入（upsert），运行时所有数据（基因命中、槽位修改、用户场景）以
  DB 为准；YAML 不再读取，只作「出厂默认」快照。
- 写入风格与 observation_store 一致（WAL + _tx 事务 + busy_timeout 兜底）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS slot_definitions(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scene           TEXT NOT NULL,
    slot_key        TEXT NOT NULL,
    slot_name       TEXT NOT NULL DEFAULT '',
    extractor_type  TEXT NOT NULL DEFAULT 'regex',   -- css | regex | jsonpath | text
    pattern         TEXT NOT NULL DEFAULT '',
    value_type      TEXT NOT NULL DEFAULT 'text',     -- text | number | money | date | url
    required        INTEGER NOT NULL DEFAULT 0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_slot_defs_scene_key ON slot_definitions(scene, slot_key);

CREATE TABLE IF NOT EXISTS document_fingerprints(
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    document_hash    TEXT NOT NULL,
    source_url       TEXT NOT NULL DEFAULT '',
    document_type    TEXT NOT NULL DEFAULT 'text',    -- html | json | pdf | text
    extracted_at     TEXT NOT NULL,
    extractor_version TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_fp_hash ON document_fingerprints(document_hash);

CREATE TABLE IF NOT EXISTS extraction_candidates(
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id    INTEGER NOT NULL,
    slot_id        INTEGER NOT NULL,
    value_json     TEXT NOT NULL,        -- JSON 序列化后的槽位值
    confidence     REAL NOT NULL DEFAULT 0.0,
    evidence_json  TEXT NOT NULL DEFAULT '{}',
    accepted       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES document_fingerprints(id),
    FOREIGN KEY(slot_id) REFERENCES slot_definitions(id)
);

CREATE TABLE IF NOT EXISTS selector_genes(
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scene          TEXT NOT NULL,
    slot_key       TEXT NOT NULL,
    selector       TEXT NOT NULL,
    selector_type  TEXT NOT NULL DEFAULT 'css',
    fitness        REAL NOT NULL DEFAULT 0.0,
    hits           INTEGER NOT NULL DEFAULT 0,
    misses         INTEGER NOT NULL DEFAULT 0,
    parent_id      INTEGER,
    enabled        INTEGER NOT NULL DEFAULT 1,
    last_used_at   TEXT,
    created_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_genes_scene_slot_sel ON selector_genes(scene, slot_key, selector);
"""


@dataclass(frozen=True, slots=True)
class SlotDefinition:
    """槽位定义（从文档中抽取的信息目标）。"""

    scene: str
    slot_key: str
    slot_name: str = ""
    extractor_type: str = "regex"  # css | regex | jsonpath | text
    pattern: str = ""
    value_type: str = "text"
    required: bool = False


@dataclass(slots=True)
class SceneDocument:
    """一次文档抽取记录（指纹去重后写入）。"""

    document_hash: str
    source_url: str = ""
    document_type: str = "text"
    extractor_version: str = ""
    extracted_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))


class SceneStore:
    """场景/槽位/基因持久化门面（单库四表）。"""

    def __init__(self, db_path: Path | str) -> None:
        path = db_path if isinstance(db_path, Path) else Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._conn = sqlite3.connect(str(path), timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> SceneStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._conn.commit()
        finally:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ── 槽位定义 ───────────────────────────────────────────
    def upsert_slot(self, definition: SlotDefinition) -> int:
        """写入或更新槽位定义（scene+slot_key 唯一），返回 slot id。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._tx() as con:
            con.execute(
                """INSERT INTO slot_definitions(
                    scene, slot_key, slot_name, extractor_type, pattern,
                    value_type, required, enabled, created_at
                ) VALUES(?,?,?,?,?,?,?,1,?)
                ON CONFLICT(scene, slot_key) DO UPDATE SET
                    slot_name=excluded.slot_name,
                    extractor_type=excluded.extractor_type,
                    pattern=excluded.pattern,
                    value_type=excluded.value_type,
                    required=excluded.required,
                    enabled=1
                """,
                (
                    definition.scene,
                    definition.slot_key,
                    definition.slot_name,
                    definition.extractor_type,
                    definition.pattern,
                    definition.value_type,
                    int(definition.required),
                    now,
                ),
            )
            row = con.execute(
                "SELECT id FROM slot_definitions WHERE scene=? AND slot_key=?",
                (definition.scene, definition.slot_key),
            ).fetchone()
            return int(row["id"])

    def get_slots(self, scene: str) -> list[SlotDefinition]:
        with self._tx() as con:
            rows = con.execute(
                "SELECT * FROM slot_definitions WHERE scene=? AND enabled=1 ORDER BY id",
                (scene,),
            ).fetchall()
        return [
            SlotDefinition(
                scene=row["scene"],
                slot_key=row["slot_key"],
                slot_name=row["slot_name"],
                extractor_type=row["extractor_type"],
                pattern=row["pattern"],
                value_type=row["value_type"],
                required=bool(row["required"]),
            )
            for row in rows
        ]

    def list_scenes(self) -> list[dict[str, Any]]:
        with self._tx() as con:
            rows = con.execute(
                """SELECT scene, COUNT(*) AS slot_count FROM slot_definitions
                   GROUP BY scene ORDER BY scene"""
            ).fetchall()
            scenes = [dict(row) for row in rows]
            for scene in scenes:
                gene_count = con.execute(
                    "SELECT COUNT(*) AS n FROM selector_genes WHERE scene=? AND enabled=1",
                    (scene["scene"],),
                ).fetchone()["n"]
                scene["gene_count"] = int(gene_count)
        return scenes

    # ── 文档指纹 ───────────────────────────────────────────
    def document_seen(self, document_hash: str) -> bool:
        with self._tx() as con:
            row = con.execute(
                "SELECT 1 FROM document_fingerprints WHERE document_hash=?",
                (document_hash,),
            ).fetchone()
        return row is not None

    def get_or_create_document(self, document: SceneDocument) -> int:
        """按内容指纹去重：已存在返回既有 id，否则写入新记录。"""
        with self._tx() as con:
            row = con.execute(
                "SELECT id FROM document_fingerprints WHERE document_hash=?",
                (document.document_hash,),
            ).fetchone()
            if row is not None:
                return int(row["id"])
            cursor = con.execute(
                """INSERT INTO document_fingerprints(
                    document_hash, source_url, document_type, extracted_at, extractor_version
                ) VALUES(?,?,?,?,?)""",
                (
                    document.document_hash,
                    document.source_url,
                    document.document_type,
                    document.extracted_at,
                    document.extractor_version,
                ),
            )
            return int(cursor.lastrowid)

    # ── 抽取候选 ───────────────────────────────────────────
    def add_candidate(
        self,
        document_id: int,
        slot_id: int,
        value: Any,
        *,
        confidence: float = 1.0,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        with self._tx() as con:
            con.execute(
                """INSERT INTO extraction_candidates(
                    document_id, slot_id, value_json, confidence, evidence_json, accepted, created_at
                ) VALUES(?,?,?,?,?,0,?)""",
                (
                    document_id,
                    slot_id,
                    json.dumps(value, ensure_ascii=False, default=str),
                    float(confidence),
                    json.dumps(evidence or {}, ensure_ascii=False, default=str),
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                ),
            )

    def accept_candidate(self, candidate_id: int) -> None:
        with self._tx() as con:
            con.execute(
                "UPDATE extraction_candidates SET accepted=1 WHERE id=?",
                (candidate_id,),
            )

    def candidates(
        self,
        *,
        scene: str | None = None,
        accepted: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if scene:
            where.append("s.scene=?")
            params.append(scene)
        if accepted is not None:
            where.append("c.accepted=?")
            params.append(int(accepted))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(limit)
        with self._tx() as con:
            rows = con.execute(
                f"""SELECT c.id, c.slot_id, c.value_json, c.confidence, c.evidence_json,
                           c.accepted, c.created_at, s.slot_key, s.slot_name
                    FROM extraction_candidates c
                    JOIN slot_definitions s ON s.id = c.slot_id
                    {clause}
                    ORDER BY c.id DESC LIMIT ?""",
                params,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["value"] = json.loads(item.pop("value_json"))
            item["evidence"] = json.loads(item.pop("evidence_json"))
            item["accepted"] = bool(item["accepted"])
            result.append(item)
        return result

    # ── 选择器基因 ─────────────────────────────────────────
    @staticmethod
    def _fitness_sql() -> str:
        return (
            "CASE WHEN (hits + misses) = 0 THEN 0.0 "
            "ELSE hits * 1.0 / (hits + misses) END"
        )

    def upsert_gene(
        self,
        scene: str,
        slot_key: str,
        selector: str,
        *,
        selector_type: str = "css",
        parent_id: int | None = None,
    ) -> int:
        """写入或更新基因（scene+slot_key+selector 唯一），返回基因 id。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._tx() as con:
            con.execute(
                """INSERT INTO selector_genes(
                    scene, slot_key, selector, selector_type, fitness,
                    hits, misses, parent_id, enabled, last_used_at, created_at
                ) VALUES(?,?,?,?,0.0,0,0,?,1,?,?)
                ON CONFLICT(scene, slot_key, selector) DO UPDATE SET
                    selector_type=excluded.selector_type,
                    enabled=1
                """,
                (scene, slot_key, selector, selector_type, parent_id, now, now),
            )
            row = con.execute(
                """SELECT id FROM selector_genes
                   WHERE scene=? AND slot_key=? AND selector=?""",
                (scene, slot_key, selector),
            ).fetchone()
            return int(row["id"])

    def record_gene_result(self, gene_id: int, *, hit: bool) -> None:
        """记录一次基因命中/未命中，并刷新 fitness。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._tx() as con:
            row = con.execute(
                "SELECT hits, misses FROM selector_genes WHERE id=?", (gene_id,)
            ).fetchone()
            if row is None:
                return
            hits, misses = int(row["hits"]), int(row["misses"])
            if hit:
                hits += 1
            else:
                misses += 1
            fitness = float(hits / (hits + misses)) if (hits + misses) else 0.0
            con.execute(
                """UPDATE selector_genes SET
                    hits=?, misses=?, fitness=?, last_used_at=? WHERE id=?""",
                (hits, misses, round(fitness, 4), now, gene_id),
            )

    def top_genes(
        self,
        scene: str,
        slot_key: str | None = None,
        *,
        limit: int = 5,
        min_trials: int = 0,
    ) -> list[dict[str, Any]]:
        """按 fitness 取最优基因；min_trials>0 时只考虑有足够尝试次数的基因。"""
        where = ["scene=?", "enabled=1"]
        params: list[Any] = [scene]
        if slot_key:
            where.append("slot_key=?")
            params.append(slot_key)
        if min_trials > 0:
            where.append("(hits + misses) >= ?")
            params.append(min_trials)
        clause = " AND ".join(where)
        params.append(limit)
        with self._tx() as con:
            rows = con.execute(
                f"""SELECT * FROM selector_genes
                    WHERE {clause}
                    ORDER BY {self._fitness_sql()} DESC, hits DESC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_genes(
        self,
        scene: str | None = None,
        *,
        min_fitness: float = 0.2,
        min_trials: int = 3,
    ) -> int:
        """淘汰低适应度基因（尝试次数达标且 fitness < 阈值），返回删除数。"""
        where = ["enabled=1", "(hits + misses) >= ?", "fitness < ?"]
        params: list[Any] = [min_trials, float(min_fitness)]
        if scene:
            where.append("scene=?")
            params.append(scene)
        clause = " AND ".join(where)
        with self._tx() as con:
            cursor = con.execute(f"DELETE FROM selector_genes WHERE {clause}", params)
            return int(cursor.rowcount)

    def prune_candidates(
        self,
        scene: str | None = None,
        *,
        min_fitness: float = 0.2,
        min_trials: int = 3,
    ) -> list[dict[str, Any]]:
        """只读预览将淘汰的基因（与 prune_genes 同条件，供 CLI 干跑）。"""
        where = ["enabled=1", "(hits + misses) >= ?", "fitness < ?"]
        params: list[Any] = [min_trials, float(min_fitness)]
        if scene:
            where.append("scene=?")
            params.append(scene)
        clause = " AND ".join(where)
        with self._tx() as con:
            rows = con.execute(
                f"""SELECT id, scene, slot_key, selector, selector_type,
                           fitness, hits, misses
                    FROM selector_genes
                    WHERE {clause}
                    ORDER BY fitness ASC, id""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def gene_stats(self) -> dict[str, Any]:
        with self._tx() as con:
            total = con.execute("SELECT COUNT(*) AS n FROM selector_genes").fetchone()["n"]
            enabled = con.execute(
                "SELECT COUNT(*) AS n FROM selector_genes WHERE enabled=1"
            ).fetchone()["n"]
            avg = con.execute("SELECT AVG(fitness) AS f FROM selector_genes").fetchone()["f"]
        return {
            "total": int(total),
            "enabled": int(enabled),
            "avg_fitness": round(float(avg or 0.0), 4),
        }

    # ── 场景 YAML 导入（bundled 默认 → DB 单一真源）──────────
    def import_scene_yaml(self, yaml_text: str) -> dict[str, Any]:
        """把场景 YAML（只读默认快照）幂等导入 DB。

        YAML 结构：``{scene, name, slots: [...], genes: [...]}``。
        slots 项含 key/name/extractor/pattern/value_type/required；
        genes 项含 slot/selector/selector_type。
        """
        import yaml

        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict) or not data.get("scene"):
            raise ValueError("场景 YAML 缺少 scene 字段")
        scene = str(data["scene"])
        slot_ids: dict[str, int] = {}
        for item in data.get("slots", []):
            if not isinstance(item, dict) or not item.get("key"):
                continue
            definition = SlotDefinition(
                scene=scene,
                slot_key=str(item["key"]),
                slot_name=str(item.get("name", "")),
                extractor_type=str(item.get("extractor", "regex")),
                pattern=str(item.get("pattern", "")),
                value_type=str(item.get("value_type", "text")),
                required=bool(item.get("required", False)),
            )
            slot_ids[definition.slot_key] = self.upsert_slot(definition)
        imported_genes = 0
        for item in data.get("genes", []):
            if not isinstance(item, dict) or not item.get("slot"):
                continue
            self.upsert_gene(
                scene,
                str(item["slot"]),
                str(item.get("selector", "")),
                selector_type=str(item.get("selector_type", "regex")),
            )
            imported_genes += 1
        return {
            "scene": scene,
            "slots": len(slot_ids),
            "genes": imported_genes,
        }

    def import_bundled_scenes(self) -> dict[str, Any]:
        """导入包内 scenes/*.yaml 出厂默认（幂等）。"""
        bundle_dir = Path(__file__).resolve().parent.parent / "scenes"
        total: dict[str, Any] = {"scenes": 0, "slots": 0, "genes": 0}
        for path in sorted(bundle_dir.glob("*.yaml")):
            result = self.import_scene_yaml(path.read_text(encoding="utf-8"))
            total["scenes"] += 1
            total["slots"] += result["slots"]
            total["genes"] += result["genes"]
        return total


__all__ = [
    "SceneDocument",
    "SceneStore",
    "SlotDefinition",
]
