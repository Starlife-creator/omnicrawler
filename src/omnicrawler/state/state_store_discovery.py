"""Observed discovery paths and bounded, explicit omission reports."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..core.utils import json_text, utcnow


class DiscoveryMixin:
    conn: Any
    _lock: Any

    if TYPE_CHECKING:
        @staticmethod
        def _require_run_id(run_id: str | None) -> str | None: ...

    def save_discovery_edges(self, run_id: str, parent: str, edges: list[dict[str, Any]]) -> None:
        self._require_run_id(run_id)
        now = utcnow()
        with self._lock, self.conn:
            self.conn.executemany(
                "INSERT INTO stage_checkpoints(run_id, stage, idempotency_key, status, payload_json, updated_at) "
                "VALUES(?, 'discovery_edge', ?, ?, ?, ?) ON CONFLICT(run_id, stage, idempotency_key) "
                "DO UPDATE SET status=excluded.status, payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                [(run_id, parent + ':' + edge['fingerprint'], edge['decision'], json_text(edge), now) for edge in edges],
            )

            self.conn.execute(
                "INSERT INTO stage_checkpoints(run_id, stage, idempotency_key, status, payload_json, updated_at) "
                "VALUES(?, 'discovery_inventory', ?, 'recorded', ?, ?) ON CONFLICT(run_id, stage, idempotency_key) "
                "DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (run_id, parent, json_text({'observed_edges': len(edges)}), now),
            )

    def clear_discovery_retry(self, fingerprint: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE frontier SET meta_json=json_remove(meta_json, '$.rediscover') WHERE fingerprint=?",
                              (fingerprint,))

    def discovery_coverage(self, run_id: str, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        self._require_run_id(run_id)
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
            raise ValueError('coverage limit must be 1..1000 and offset must be nonnegative')
        with self._lock:
            if not self.conn.execute('SELECT 1 FROM runs WHERE run_id=?', (run_id,)).fetchone():
                raise ValueError('coverage run does not exist')
            decisions = {row['status']: row['n'] for row in self.conn.execute(
                "SELECT status, COUNT(*) n FROM stage_checkpoints WHERE run_id=? AND stage='discovery_edge' GROUP BY status",
                (run_id,),
            )}
            gap_sql = (
                " FROM stage_checkpoints e LEFT JOIN frontier f "
                "ON f.fingerprint=json_extract(e.payload_json, '$.fingerprint') "
                "WHERE e.run_id=? AND e.stage='discovery_edge' "
                "AND e.status IN ('depth_limit','topic_prefilter','scope_rejected','follow_filter') "
                "AND (f.status IS NULL OR f.status!='done')"
            )
            total = self.conn.execute('SELECT COUNT(*)' + gap_sql, (run_id,)).fetchone()[0]
            unique = self.conn.execute("SELECT COUNT(DISTINCT json_extract(e.payload_json, '$.fingerprint'))" + gap_sql,
                                       (run_id,)).fetchone()[0]
            rows = self.conn.execute('SELECT e.payload_json, f.status frontier_status' + gap_sql +
                                     ' ORDER BY e.idempotency_key LIMIT ? OFFSET ?', (run_id, limit, offset)).fetchall()
            gaps = [{**json.loads(row['payload_json']), 'frontier_status': row['frontier_status']} for row in rows]
            queue = {row['status']: row['n'] for row in self.conn.execute('SELECT status, COUNT(*) n FROM frontier GROUP BY status')}
            steps = self.conn.execute(
                "SELECT COUNT(*), SUM(status!='succeeded') FROM stage_checkpoints WHERE run_id=? AND stage='discover'",
                (run_id,),
            ).fetchone()
            pending = sum(queue.get(key, 0) for key in ('pending', 'in_progress', 'failed', 'blocked'))
            historical_sql = gap_sql.replace('e.run_id=?', 'e.run_id!=?')
            historical = self.conn.execute('SELECT COUNT(*)' + historical_sql, (run_id,)).fetchone()[0]
            history_runs = self.conn.execute('SELECT e.run_id, COUNT(*) n' + historical_sql +
                                            ' GROUP BY e.run_id ORDER BY e.run_id LIMIT 20', (run_id,)).fetchall()
            history_total = self.conn.execute('SELECT COUNT(DISTINCT e.run_id)' + historical_sql, (run_id,)).fetchone()[0]
            inventoried = self.conn.execute(
                "SELECT COUNT(*) FROM stage_checkpoints WHERE run_id=? AND stage='discovery_inventory'", (run_id,),
            ).fetchone()[0]
            unrecorded = max(0, steps[0] - inventoried)
            partial = bool(unique or pending or steps[1] or historical)
            return {
                'run_id': run_id, 'site_coverage': 'unknown',
                'observed_traversal': 'partial' if partial else 'observed_paths_exhausted' if steps[0] and not unrecorded else 'unknown',
                'discovery_decisions': decisions, 'known_discovery_gaps': unique,
                'gap_paths_total': total, 'gap_paths': gaps, 'offset': offset, 'limit': limit,
                'truncated': offset + len(gaps) < total,
                'next_offset': offset + len(gaps) if offset + len(gaps) < total else None,
                'workspace_frontier': queue, 'workspace_unfinished_requests': pending,
                'historical_gap_paths': historical, 'historical_gap_runs': [dict(row) for row in history_runs],
                'historical_runs_total': history_total, 'historical_runs_truncated': history_total > len(history_runs),
                'unrecorded_discovery_steps': unrecorded,
                'discovery_steps': steps[0], 'failed_discovery_steps': steps[1] or 0,
                'scope': 'Discovery decisions belong to this run; frontier is the current workspace state.',
                'limitation': 'Only observed eligible links are counted; unseen pages and source-internal filters without diagnostics are unknown. Historical gaps may be stale and require review.',
            }
