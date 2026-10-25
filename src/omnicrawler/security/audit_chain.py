"""A-P2-2：追加式审计哈希链（JSONL + SQLite ``audit_events`` 联动）。

每条审计记录携带：

- ``seq``：单调递增序号；
- ``prev_hash``：上一条记录的 ``record_hash``（首条为 64 个零）；
- ``record_hash``：除自身哈希字段外全部载荷规范化 JSON 的 SHA-256。

重放校验检查序号连续、``prev_hash`` 链接正确、``record_hash`` 重算一致；
``strict=True``（配置 ``audit.strict_chain``）时校验失败 fail-closed 抛
:class:`AuditChainBrokenError`，非严格模式只返回校验报告、不阻断流程。

JSONL 是主追加日志（可离线重放），SQLite ``audit_events`` 是任务库内的交叉
留存，``chain_seq`` / ``prev_hash`` / ``record_hash`` 三列与 JSONL 同源写入。
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from ..core.utils import json_text, utcnow

GENESIS_HASH = "0" * 64


class AuditChainBrokenError(RuntimeError):
    """严格模式下审计哈希链校验失败（内容被篡改 / 链接断裂 / 序号不连续）。"""


def record_hash(payload: dict[str, Any]) -> str:
    """计算载荷规范化 JSON 的 SHA-256 十六进制摘要（键排序、不转义非 ASCII）。"""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditChain:
    """线程安全的追加式审计哈希链。"""

    def __init__(self, path: Path, *, strict: bool = False, state: Any = None) -> None:
        self.path = Path(path)
        self.strict = strict
        self._state = state
        self._lock = threading.Lock()
        self._last_seq = 0
        self._last_hash = GENESIS_HASH
        self._bootstrap()

    # ── 内部 ────────────────────────────────────────────────────────

    def _bootstrap(self) -> None:
        """从 JSONL 尾部恢复链头（SQLite 不承担权威序号，避免跨进程序号漂移）。"""
        if not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            self._last_seq = int(record.get("seq", self._last_seq))
            self._last_hash = str(record.get("record_hash", self._last_hash))

    # ── 写入 ────────────────────────────────────────────────────────

    def append(
        self,
        event: dict[str, Any],
        *,
        run_id: str | None = None,
        action: str = "audit",
        actor: str = "audit-chain",
    ) -> dict[str, Any]:
        """追加一条审计记录，返回落库记录（含 ``seq`` / ``prev_hash`` / ``record_hash``）。"""
        with self._lock:
            seq = self._last_seq + 1
            payload: dict[str, Any] = {
                "seq": seq,
                "timestamp": utcnow(),
                "run_id": run_id,
                "action": action,
                "actor": actor,
                "event": event,
                "prev_hash": self._last_hash,
            }
            digest = record_hash(payload)
            record = {**payload, "record_hash": digest}

            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

            if self._state is not None:
                with self._state._lock, self._state.conn:
                    self._state.conn.execute(
                        """
                        INSERT INTO audit_events(
                            run_id, action, actor, details_json, created_at,
                            chain_seq, prev_hash, record_hash
                        ) VALUES(?,?,?,?,?,?,?,?)
                        """,
                        (
                            run_id, action, actor, json_text(payload),
                            payload["timestamp"], seq, self._last_hash, digest,
                        ),
                    )

            self._last_seq = seq
            self._last_hash = digest
            return record

    # ── 重放校验 ──────────────────────────────────────────────────────

    def verify(self) -> dict[str, Any]:
        """重放 JSONL 全链，返回 ``{ok, verified, broken}``；严格模式失败抛错。"""
        broken: list[dict[str, Any]] = []
        prev_hash = GENESIS_HASH
        expected_seq = 1
        verified = 0

        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                verified += 1
                problems: list[str] = []
                seq = record.get("seq")
                if seq != expected_seq:
                    problems.append(f"序号不连续：期望 {expected_seq}，实际 {seq}")
                if record.get("prev_hash") != prev_hash:
                    problems.append("prev_hash 链接断裂")
                payload = {key: value for key, value in record.items() if key != "record_hash"}
                if record_hash(payload) != record.get("record_hash"):
                    problems.append("record_hash 重算不一致（载荷被篡改）")
                if problems:
                    broken.append({"seq": seq, "problems": problems})
                prev_hash = str(record.get("record_hash", prev_hash))
                expected_seq += 1

        ok = not broken
        if self.strict and not ok:
            raise AuditChainBrokenError(
                f"审计哈希链校验失败：{len(broken)} 条记录异常（共 {verified} 条）"
            )
        return {"ok": ok, "verified": verified, "broken": broken}
