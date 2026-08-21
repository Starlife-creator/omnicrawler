"""H1 安全指标收集器（Phase 2a；入 diagnostics，retention 已有 config）。

指标全集（第 49 轮全部正式纳入，无"可选/裁剪"）——计数器语义，90 天滚动
窗口口径由消费方（diagnostics）解释；此处只做无副作用累加与快照导出。

线程安全：单一锁保护计数表。审计健康增补（第 65 轮）：audit_lag_ms 累计
与 audit_dropped_total 单独计数（审计管道健康可见，缺口窗口明确）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

# 指标全集（H1 第 49 轮 + 第 65 轮审计健康增补）
METRIC_KEYS = (
    "isolation_subprocess_total",       # subprocess 隔离运行总数（隔离率分子）
    "isolation_in_process_total",       # in_process 运行总数（隔离率分母组成）
    "e_resource_total",                 # E_RESOURCE 计数（突增告警源）
    "e_permission_total",               # E_PERMISSION 计数
    "e_quota_total",                    # E_QUOTA 计数
    "sandbox_failure_total",            # 沙箱失败计数（沙箱失败率分子）
    "sandbox_unavailable_env_total",    # 沙箱不可用环境数（fail-closed 环境性拒载）
    "download_hash_mismatch_total",     # 下载哈希不匹配计数（G1）
    "revocation_hit_total",             # 吊销命中数（G2）
    "allowlist_hit_total",              # 豁免表命中数（含过期失效）
    "allowlist_expired_total",          # 豁免表过期失效数
    "subprocess_forced_total",          # subprocess 强制数（force_subprocess 总闸）
    "gate_denied_total",                # 四门禁拒绝计数
    "secret_accessed_total",            # 密钥访问审计计数（O 例外路径）
    "session_crash_total",              # 会话崩溃计数
    "session_started_total",            # 会话启动计数
    "degraded_use_total",               # 降级使用计数（防腐化联动 H7）
    "egress_cooccurrence_risk_total",   # 同会话 read/fetch 共现风险提示（data_egress_policy）
    "audit_dropped_total",              # 审计丢弃计数（第 65 轮）
    "audit_lag_ms_total",               # 审计队列延迟累计 ms（第 65 轮，配 audit_lag_count 求均值）
    "audit_lag_count",
    "in_process_granted_total",         # 特权批准计数（季度复核输入）
)


@dataclass(slots=True)
class SecurityMetrics:
    """H1 安全指标计数表（线程安全累加 + 快照导出）。"""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _counters: dict[str, int] = field(default_factory=dict, repr=False)
    _started_at: float = field(default_factory=time.monotonic, repr=False)

    def __post_init__(self) -> None:
        self._counters = {key: 0 for key in METRIC_KEYS}

    def increment(self, key: str, amount: int = 1) -> None:
        """累加单个指标；未知键拒绝（防指标拼写漂移，H7 语义）。"""
        if key not in METRIC_KEYS:
            raise KeyError(f"未知安全指标: {key}（合法键: {METRIC_KEYS}）")
        with self._lock:
            self._counters[key] += amount

    def value(self, key: str) -> int:
        if key not in METRIC_KEYS:
            raise KeyError(f"未知安全指标: {key}")
        with self._lock:
            return self._counters[key]

    def snapshot(self) -> dict[str, int]:
        """全量指标快照（diagnostics 消费；含 uptime_seconds 供窗口解释）。"""
        with self._lock:
            result = dict(self._counters)
        result["uptime_seconds"] = int(time.monotonic() - self._started_at)
        return result

    def isolation_ratio(self) -> float | None:
        """隔离率：subprocess / (subprocess + in_process)；无样本返回 None。"""
        with self._lock:
            sub = self._counters["isolation_subprocess_total"]
            inproc = self._counters["isolation_in_process_total"]
        total = sub + inproc
        return (sub / total) if total else None

    def audit_lag_ms_avg(self) -> float | None:
        """审计队列平均延迟（第 65 轮）；无样本返回 None。"""
        with self._lock:
            lag_total = self._counters["audit_lag_ms_total"]
            count = self._counters["audit_lag_count"]
        return (lag_total / count) if count else None

    def iter_items(self) -> Iterator[tuple[str, int]]:
        snapshot = self.snapshot()
        return iter(sorted(snapshot.items()))
