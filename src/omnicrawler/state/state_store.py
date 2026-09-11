from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .schema import SCHEMA
from .state_store_artifacts import ArtifactsMixin
from .state_store_plugin_state import PluginStateMixin
from .state_store_quality import QualityMixin
from .state_store_queue import QueueMixin
from .state_store_records import RecordsMixin
from .state_store_runs import RunsMixin


class _ClosedConnection:
    """S2.5.42：close() 后 conn 的受控占位——任何访问抛可读错误而非 AttributeError。"""

    def __getattr__(self, _name: str) -> Any:
        raise RuntimeError("StateStore 已关闭，禁止继续操作")


class StateStore(
    ArtifactsMixin,
    PluginStateMixin,
    QualityMixin,
    QueueMixin,
    RecordsMixin,
    RunsMixin,
):
    # B04-003：run_id 参与 SQL 查询与 artifact/response 落盘路径构造，集中校验
    # 防注入/穿越（与 capsule_store._RUN_ID_RE 同源约定：纯安全字符，最长 80）。
    _RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=60, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=60000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._ensure_response_columns()
        self._lock = threading.RLock()

    @staticmethod
    def _require_run_id(run_id: str | None) -> str | None:
        """集中校验 run_id（None 放行，用于可选过滤参数；非 None 必须匹配安全字符）。"""
        if run_id is not None and (
            not isinstance(run_id, str) or not StateStore._RUN_ID_RE.fullmatch(run_id)
        ):
            raise ValueError(f"run_id 含非法字符: {run_id!r}")
        return run_id

    def _ensure_response_columns(self) -> None:
        """Upgrade existing 1.0 workspaces without rebuilding their state database."""
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(responses)")}
        with self.conn:
            if "etag" not in columns:
                self.conn.execute("ALTER TABLE responses ADD COLUMN etag TEXT")
            if "last_modified" not in columns:
                self.conn.execute("ALTER TABLE responses ADD COLUMN last_modified TEXT")


    def close(self) -> None:
        with self._lock:
            if not self.conn or isinstance(self.conn, _ClosedConnection):
                return
            self.conn.close()
            # S2.5.42：关闭后方法调用得到受控 RuntimeError，而非 AttributeError
            self.conn = _ClosedConnection()  # type: ignore[assignment]

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


    # -- 安全白名单：ORDER BY 从句紧邻 SQL 执行点 --


    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """⚠ 调试/诊断用：执行原始 SQL 查询。

        S2.5.42：只允许只读语句（SELECT/WITH/PRAGMA），拒绝写操作，
        防止注入面被滥用为数据篡改。调用方仍须保证参数化。
        优先使用 set_status / claim / mark_done 等类型安全方法。
        """
        prefix = sql.lstrip().upper()
        if not prefix.startswith(("SELECT", "WITH", "PRAGMA")):
            raise ValueError("rows() 仅允许只读查询（SELECT/WITH/PRAGMA）")
        # B04-002：调试接口鉴权审计——每次原始查询留痕（action=raw_rows_query），
        # 供事后追溯谁在什么阶段执行了什么只读查询。
        try:
            self.add_audit_event("raw_rows_query", actor="state_store.rows", details={"prefix": prefix.split()[0]})
        except Exception:  # noqa: BLE001 — 审计失败不得阻断诊断查询
            pass
        with self._lock:
            return [dict(row) for row in self.conn.execute(sql, params).fetchall()]
