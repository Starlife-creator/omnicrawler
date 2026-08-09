"""AutoPilot — 自适应执行闭环，自动调整采集参数。

融合 agent-browser 概念：AI 自主决策浏览器操作参数，
根据运行时信号自动调整并发、延迟、OCR、运行状态，
附带历史趋势分析避免误触发。

用法:
    pilot = AutoPilot(config)
    pilot.start()
    for page in pages:
        pilot.record_signals(latency=0.5, error_rate=0.01, ...)
        adjusted = pilot.maybe_adjust()
    pilot.stop()
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .adaptive_execution import AdaptiveController, Adjustment, RuntimeSignals


@dataclass
class SignalSnapshot:
    """带时间戳的信号快照。"""
    timestamp: float
    latency_seconds: float
    error_rate: float
    rate_limited: bool
    dom_stability: float
    text_layer_quality: float
    free_disk_bytes: int
    concurrency: int
    wait_seconds: float


@dataclass
class TrendAnalysis:
    """信号趋势分析结果。"""
    direction: str = "stable"       # rising / falling / stable
    severity: str = "info"          # info / warning / critical
    confidence: float = 0.0
    description: str = ""


class SignalHistory:
    """信号历史追踪器 — 区分瞬时尖峰和持续恶化。"""

    def __init__(self, window_size: int = 20) -> None:
        self._history: deque[SignalSnapshot] = deque(maxlen=window_size)

    def record(self, snapshot: SignalSnapshot) -> None:
        self._history.append(snapshot)

    def analyze(self, metric: str) -> TrendAnalysis:
        """分析指定指标的近期趋势。"""
        if len(self._history) < 3:
            return TrendAnalysis()

        values: list[float] = []
        for snap in list(self._history)[-10:]:
            val = getattr(snap, metric, 0.0)
            if isinstance(val, bool):
                val = 1.0 if val else 0.0
            values.append(float(val))

        if not values:
            return TrendAnalysis()

        # 简单线性趋势检测
        n = len(values)
        if n < 3:
            return TrendAnalysis()

        # 计算斜率（简易线性回归）
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        numerator = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator != 0 else 0

        # 归一化斜率
        range_val = max(values) - min(values)
        if range_val == 0:
            normalized_slope = 0.0
        else:
            normalized_slope = slope * n / range_val

        if normalized_slope > 0.15:
            direction = "rising"
        elif normalized_slope < -0.15:
            direction = "falling"
        else:
            direction = "stable"

        # 严重性判断
        recent = values[-3:]
        avg_recent = sum(recent) / len(recent)
        if metric in ("error_rate", "rate_limited"):
            if avg_recent > 0.5 and direction == "rising":
                severity = "critical"
            elif avg_recent > 0.2:
                severity = "warning"
            else:
                severity = "info"
        elif metric == "latency_seconds":
            if avg_recent > 10 and direction == "rising":
                severity = "critical"
            elif avg_recent > 5:
                severity = "warning"
            else:
                severity = "info"
        else:
            severity = "info"

        desc_map = {
            ("rising", "critical"): f"{metric} 持续恶化，已达危险水平",
            ("rising", "warning"): f"{metric} 呈上升趋势",
            ("falling", "info"): f"{metric} 正在改善",
            ("stable", "info"): f"{metric} 保持稳定",
        }
        description = desc_map.get((direction, severity), f"{metric} 趋势: {direction}")

        return TrendAnalysis(
            direction=direction, severity=severity,
            confidence=min(0.95, abs(normalized_slope)),
            description=description,
        )


@dataclass
class AutoPilotState:
    """AutoPilot 运行状态。"""
    running: bool = False
    current_concurrency: int = 2
    current_wait: float = 1.0
    current_ocr: bool = True
    total_adjustments: int = 0
    last_adjustment_time: float = 0.0
    adjustment_cooldown: float = 10.0  # 两次调整最小间隔（秒）
    audit: list[Adjustment] = field(default_factory=list)


class AutoPilot:
    """自适应执行闭环 — 自动监测并调整采集参数。

    特性:
        - 历史趋势分析（区分瞬时尖峰 vs 持续恶化）
        - 冷却期（避免频繁抖动）
        - 安全边界（绝不超出用户设定的上下限）
        - 完整审计链
        - GUI 友好的状态快照
    """

    def __init__(
        self,
        min_concurrency: int = 1,
        max_concurrency: int = 8,
        min_wait: float = 0.2,
        max_wait: float = 30.0,
        min_disk: int = 536_870_912,  # 512 MB
    ) -> None:
        self._controller = AdaptiveController(
            enabled=True,
            minimum_concurrency=min_concurrency,
            maximum_concurrency=max_concurrency,
            minimum_free_disk=min_disk,
        )
        self._history = SignalHistory(window_size=30)
        self._state = AutoPilotState()
        self._min_wait = min_wait
        self._max_wait = max_wait

    # ── 公共 API ──────────────────────────────────────────────────────

    def start(self, concurrency: int = 2, wait_seconds: float = 1.0, ocr: bool = True) -> None:
        self._state.running = True
        self._state.current_concurrency = concurrency
        self._state.current_wait = wait_seconds
        self._state.current_ocr = ocr
        self._state.total_adjustments = 0
        self._state.last_adjustment_time = 0.0

    def stop(self) -> AutoPilotState:
        self._state.running = False
        return self._state

    def record_signals(
        self,
        latency: float = 0.5,
        error_rate: float = 0.0,
        rate_limited: bool = False,
        dom_stability: float = 0.5,
        text_quality: float = 0.5,
        free_disk: int = 10_000_000_000,
    ) -> None:
        """记录一次运行时信号。"""
        snap = SignalSnapshot(
            timestamp=time.time(),
            latency_seconds=latency,
            error_rate=error_rate,
            rate_limited=rate_limited,
            dom_stability=dom_stability,
            text_layer_quality=text_quality,
            free_disk_bytes=free_disk,
            concurrency=self._state.current_concurrency,
            wait_seconds=self._state.current_wait,
        )
        self._history.record(snap)

    def maybe_adjust(self) -> list[Adjustment]:
        """根据当前趋势决定是否调整参数，返回实际执行的调整。"""
        if not self._state.running:
            return []

        now = time.time()
        if now - self._state.last_adjustment_time < self._state.adjustment_cooldown:
            return []

        # 获取趋势分析
        error_trend = self._history.analyze("error_rate")
        latency_trend = self._history.analyze("latency_seconds")

        # 只在严重或恶化趋势时触发调整；健康时也触发以允许并发回升（S2.5.26）
        healthy = error_trend.severity in ("ok", "info") and latency_trend.severity in ("ok", "info", "warning")
        should_adjust = (
            error_trend.severity in ("warning", "critical")
            or error_trend.direction == "rising"
            or latency_trend.severity == "critical"
            or healthy
        )

        if not should_adjust:
            return []

        # 构建当前状态
        current = {
            "concurrency": self._state.current_concurrency,
            "wait_seconds": self._state.current_wait,
            "ocr": self._state.current_ocr,
            "run_state": "running" if self._state.running else "pause",
        }
        signals = RuntimeSignals(
            latency_seconds=self._last_latency(),
            error_rate=self._last_error_rate(),
            rate_limited=self._last_rate_limited(),
            dom_stability=self._last_dom_stability(),
            text_layer_quality=self._last_text_quality(),
            free_disk_bytes=self._last_free_disk(),
        )

        proposals = self._controller.propose(current, signals)
        if not proposals:
            return []

        # 应用调整
        applied: list[Adjustment] = []
        for adj in proposals:
            if adj.parameter == "concurrency":
                self._state.current_concurrency = int(adj.after)
                applied.append(adj)
            elif adj.parameter == "wait_seconds":
                self._state.current_wait = max(self._min_wait, min(self._max_wait, float(adj.after)))
                applied.append(adj)
            elif adj.parameter == "ocr":
                self._state.current_ocr = bool(adj.after)
                applied.append(adj)
            elif adj.parameter == "run_state":
                # S2.5.26：磁盘保护提案不再被应用循环静默丢弃
                self._state.running = bool(adj.after == "running")
                applied.append(adj)

        if applied:
            self._state.total_adjustments += len(applied)
            self._state.last_adjustment_time = now
            # S2.5.27：audit 有界（200 条），不无限增长
            self._state.audit = (self._state.audit + applied)[-200:]

        return applied

    def dashboard(self) -> dict[str, Any]:
        """生成实时仪表盘数据（供 GUI 消费）。"""
        return {
            "running": self._state.running,
            "concurrency": self._state.current_concurrency,
            "wait_seconds": round(self._state.current_wait, 2),
            "ocr": self._state.current_ocr,
            "total_adjustments": self._state.total_adjustments,
            "trends": {
                "error_rate": self._history.analyze("error_rate").description,
                "latency": self._history.analyze("latency_seconds").description,
            },
            "recent_adjustments": [
                {"parameter": a.parameter, "before": a.before, "after": a.after, "reason": a.reason}
                for a in self._state.audit[-5:]
            ],
            "history_size": len(self._history._history),
        }

    # ── 辅助 ──────────────────────────────────────────────────────────

    def _last_latency(self) -> float:
        if self._history._history:
            return self._history._history[-1].latency_seconds
        return 0.5

    def _last_error_rate(self) -> float:
        if self._history._history:
            return self._history._history[-1].error_rate
        return 0.0

    def _last_rate_limited(self) -> bool:
        if self._history._history:
            return self._history._history[-1].rate_limited
        return False

    def _last_dom_stability(self) -> float:
        if self._history._history:
            return self._history._history[-1].dom_stability
        return 0.5

    def _last_text_quality(self) -> float:
        if self._history._history:
            return self._history._history[-1].text_layer_quality
        return 0.5

    def _last_free_disk(self) -> int:
        if self._history._history:
            return self._history._history[-1].free_disk_bytes
        return 10_000_000_000
