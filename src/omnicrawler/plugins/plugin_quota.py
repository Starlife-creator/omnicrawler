"""每日网络配额存储与检查（Phase 2b D4.4：双层量约束的日级层）。

与 EgressBroker `maximum_requests`（会话/能力级）构成双层量约束：
- 会话级：EgressBroker maximum_requests 原语（每会话/每能力请求上限，已有）
- 日级：本模块按 plugin_id × UTC 日期累计请求数/字节数 → 超限 E_QUOTA

持久化：JSON 文件（workspace/plugins/network_quota.json），按日滚动。
并发安全：跨进程不保证强一致（写入采用原子替换）；单进程内由调用方锁。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_date_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class DailyNetworkQuota:
    """按插件每日请求/字节配额（E_QUOTA 来源）。

    ``rules`` 结构：``{plugin_id: {"requests": N, "bytes": N}}``
    - requests/bytes 缺省不限（0 = 不限）
    - 日级计数按 UTC 日期键滚动；存储损坏 → fail-closed 拒超限检查
      （宁可错拒不可漏放）。
    """

    def __init__(self, rules: dict[str, Any] | None = None, path: Path | None = None) -> None:
        self._rules = {str(k): dict(v) for k, v in (rules or {}).items()}
        self._path = path
        self._lock = threading.Lock()
        self._days: dict[str, dict[str, dict[str, int]]] = {}

    # ---- 存储 ----

    def load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._days = {str(k): dict(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError, ValueError):
            # fail-closed：存储损坏 → 清空当日计数（触发后续 check 拒超限）
            self._days = {}

    def persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._days, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self._path)

    # ---- 配额语义 ----

    def check(self, plugin_id: str) -> None:
        """检查插件当日是否超限；超限抛 QuotaExceededError（映射 E_QUOTA）。"""
        rule = self._rules.get(plugin_id)
        if not rule:
            return  # 未配置配额 → 不限
        requests_limit = int(rule.get("requests", 0) or 0)
        bytes_limit = int(rule.get("bytes", 0) or 0)
        if requests_limit <= 0 and bytes_limit <= 0:
            return
        today = self._usage(plugin_id)
        used_requests = today.get("requests", 0)
        used_bytes = today.get("bytes", 0)
        if requests_limit > 0 and used_requests >= requests_limit:
            raise QuotaExceededError(
                f"插件 {plugin_id} 今日网络请求已达配额 {requests_limit}（decision: quota_exceeded）"
            )
        if bytes_limit > 0 and used_bytes >= bytes_limit:
            raise QuotaExceededError(
                f"插件 {plugin_id} 今日网络字节已达配额 {bytes_limit}"
            )

    def account(self, plugin_id: str, *, requests: int = 1, bytes_: int = 0) -> None:
        """累计用量（fetch 成功/失败都计——防恶意重试刷配额）。"""
        if plugin_id not in self._rules:
            return
        with self._lock:
            day = self._days.setdefault(_utc_date_key(), {})
            plugin_usage = day.setdefault(plugin_id, {"requests": 0, "bytes": 0})
            plugin_usage["requests"] += requests
            plugin_usage["bytes"] += bytes_

    def _usage(self, plugin_id: str) -> dict[str, int]:
        with self._lock:
            day = self._days.get(_utc_date_key(), {})
            return dict(day.get(plugin_id, {"requests": 0, "bytes": 0}))


class QuotaExceededError(Exception):
    """每日配额超限（映射协议错误码 E_QUOTA）。"""
