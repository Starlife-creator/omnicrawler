"""StateStore 的「插件状态」域 —— 从 state_store.py 抽出的 Mixin（P1-3 第一批）。

覆盖插件命名空间状态的读写/删除与跨 schema 复制：
``plugin_state_get`` / ``plugin_state_set`` / ``plugin_state_delete`` /
``plugin_state_copy_schema`` / ``_require_plugin_state_key`` + ``_PLUGIN_STATE_KEY_RE``。

以 Mixin 形式保留 ``self`` 语义（宿主 ``conn`` / ``_lock`` 不变），调用点零改动。
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..core.utils import utcnow


class PluginStateMixin:
    """插件状态域：命名空间隔离的键值存取。"""

    # ---- 宿主契约：实例属性 ----
    conn: sqlite3.Connection
    _lock: Any

    _PLUGIN_STATE_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
    def plugin_state_get(
        self,
        namespace: tuple[str, str, str, int],
        key: str,
    ) -> tuple[bool, Any | None]:
        self._require_plugin_state_key(key)
        with self._lock:
            row = self.conn.execute(
                "SELECT value_json FROM plugin_state WHERE project_scope=? AND plugin_id=? "
                "AND author_fingerprint=? AND schema_version=? AND state_key=?",
                (*namespace, key),
            ).fetchone()
        if row is None:
            return False, None
        return True, json.loads(row["value_json"])

    def plugin_state_set(
        self,
        namespace: tuple[str, str, str, int],
        key: str,
        value: Any,
    ) -> None:
        self._require_plugin_state_key(key)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("单个插件状态值不得超过 64 KiB")
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO plugin_state(project_scope, plugin_id, author_fingerprint, "
                "schema_version, state_key, value_json, updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(project_scope, plugin_id, author_fingerprint, schema_version, state_key) "
                "DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (*namespace, key, encoded, utcnow()),
            )

    def plugin_state_delete(
        self,
        namespace: tuple[str, str, str, int],
        key: str,
    ) -> bool:
        self._require_plugin_state_key(key)
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "DELETE FROM plugin_state WHERE project_scope=? AND plugin_id=? "
                "AND author_fingerprint=? AND schema_version=? AND state_key=?",
                (*namespace, key),
            )
        return cursor.rowcount > 0

    def plugin_state_copy_schema(
        self,
        namespace: tuple[str, str, str, int],
        source_schema: int,
    ) -> int:
        project_scope, plugin_id, author_fingerprint, target_schema = namespace
        if source_schema < 1 or source_schema >= target_schema:
            raise ValueError("插件状态迁移源版本必须小于当前 schema_version")
        with self._lock, self.conn:
            occupied = self.conn.execute(
                "SELECT 1 FROM plugin_state WHERE project_scope=? AND plugin_id=? "
                "AND author_fingerprint=? AND schema_version=? LIMIT 1",
                namespace,
            ).fetchone()
            if occupied is not None:
                raise ValueError("目标插件状态命名空间非空，拒绝覆盖迁移")
            cursor = self.conn.execute(
                "INSERT INTO plugin_state(project_scope, plugin_id, author_fingerprint, "
                "schema_version, state_key, value_json, updated_at) "
                "SELECT project_scope, plugin_id, author_fingerprint, ?, state_key, value_json, ? "
                "FROM plugin_state WHERE project_scope=? AND plugin_id=? "
                "AND author_fingerprint=? AND schema_version=?",
                (
                    target_schema,
                    utcnow(),
                    project_scope,
                    plugin_id,
                    author_fingerprint,
                    source_schema,
                ),
            )
        return cursor.rowcount

    @classmethod
    def _require_plugin_state_key(cls, key: str) -> None:
        if not isinstance(key, str) or cls._PLUGIN_STATE_KEY_RE.fullmatch(key) is None:
            raise ValueError(f"插件状态键非法: {key!r}")
