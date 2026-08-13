"""P2-4：统一异步任务进度协议（ConvertX 思路仅提取进度协议部分，不引任务队列）。

设计目标：
1. 单一 ``TaskProgressEvent`` dataclass 同时承载：阶段名、百分比、子项计数、ETA、状态、瞬时速率。
2. 阶段权重声明 → 阶段完成推进百分，避免 B12 那种手工 `第几阶段/总阶段`。
3. EMA 平滑 ETA：按最近 5 次进度样本估算瞬时速率，过滤抖动。
4. 可序列化：``.to_log_line()`` → ``PROGRESS2: {...}`` 写 CLI 日志，LogParser 消费后还原为事件，
   CLI/GUI 共用同一协议（WorkerTaskRunner 不再只解析百分 + URL）。
5. 最小增量：旧代码 ``progress.emit(percent)`` / ``document_progress.emit(x, y)`` 不改；
   新协议通过桥接方法从旧信号转换生成，老消费者不受影响。
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

LOG_PREFIX = "PROGRESS2:"
_EMA_ALPHA = 0.35  # 指数平滑系数（越大越敏感于最新瞬时速率）
_EMA_WARMUP = 5    # 至少几个样本后才输出 ETA（避免起步 0% 时的荒谬估算）


@dataclass
class StageSpec:
    """流水线阶段权重声明。

    权重可以是任意正数，系统内部会做归一化：
        stage_span = weight / sum(all.weights)
    """

    name: str                # 阶段名（英文稳定 ID；展示端映射 i18n）
    weight: float = 1.0
    display_name: str = ""   # 可选中文展示名
    # 该阶段是否包含「子项计数」子进度（例如抽取 N 份文档 / 导出 N 条记录）
    has_items: bool = False
    # 阶段预期子项总数（可选，已知时用于子项百分展开）
    expected_items: int = 0


@dataclass
class TaskProgressEvent:
    """统一进度事件。任何线程/CLI 日志都可还原为同一对象。"""

    task_id: str = ""
    stage: str = ""          # 当前阶段（对应 StageSpec.name；空串=整体）
    display_stage: str = ""  # 中文阶段标签（空=消费方用 stage）
    percent: float = 0.0     # 0.0 - 100.0；超过 100 按 100 显示
    state: str = "running"   # idle | running | paused | finished | failed | cancelled
    # 子项计数（阶段内，如已处理文档）：当前 / 总数
    item_current: int = 0
    item_total: int = 0
    # 统计（可选）
    rate: float = 0.0        # 瞬时速率（单位由消费方约定：页/s、文档/s...）
    rate_unit: str = ""      # 速率单位，用于展示
    eta_seconds: float = 0.0  # 剩余秒数（0 表示未知；未达 EMA 样本量也为 0）
    started_at: float = 0.0   # time.time() 开始时间戳
    updated_at: float = 0.0   # time.time() 本事件时间戳
    message: str = ""         # 额外提示文本（如「正在处理 xxx.pdf」）
    extra: dict[str, Any] = field(default_factory=dict)  # 自定义负载（含 warning 等）

    # ── 序列化 ────────────────────────────────────────────
    def to_log_line(self) -> str:
        """序列化为 CLI 日志行，用于 Worker 进程输出。"""
        payload = {
            k: v for k, v in asdict(self).items()
            if v not in (0, 0.0, "", {}, [], False, None)
        }
        return f"{LOG_PREFIX} {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"

    @classmethod
    def from_log_line(cls, line: str) -> TaskProgressEvent | None:
        """从日志行还原事件；非 PROGRESS2 行返回 None。"""
        if LOG_PREFIX not in line:
            return None
        idx = line.find(LOG_PREFIX)
        body = line[idx + len(LOG_PREFIX):].strip()
        if not body:
            return None
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProgressTracker:
    """流水线进度计算：阶段权重 + 子项展开 + EMA ETA。

    用法（worker 端）：

        tracker = ProgressTracker(stages=[
            StageSpec("ingest",  weight=1, display_name="扫描", has_items=True),
            StageSpec("parse",   weight=2, display_name="解析文字层"),
            StageSpec("extract", weight=3, display_name="结构化抽取", has_items=True),
            StageSpec("export",  weight=1, display_name="导出"),
        ])
        tracker.start()
        tracker.begin_stage("ingest", expected_items=12)
        # 每扫完一个
        tracker.advance_item()
        tracker.end_stage("ingest")
        # ...其他阶段...
        tracker.finish()

    期间通过 ``on_event`` 回调持续向外推送 ``TaskProgressEvent``；
    同时 ``.last_event`` 缓存最新事件供 GUI 轮询。
    """

    def __init__(
        self,
        stages: Sequence[StageSpec],
        *,
        task_id: str = "",
        on_event: Callable[[TaskProgressEvent], None] | None = None,
    ) -> None:
        if not stages:
            raise ValueError("stages 不能为空")
        self._stages: list[StageSpec] = list(stages)
        self._task_id = task_id
        self._on_event = on_event

        total_w = sum(max(0.0, s.weight) for s in self._stages)
        if total_w <= 0:
            raise ValueError("stages 权重和必须为正")
        self._stage_span: dict[str, float] = {s.name: s.weight / total_w * 100.0 for s in self._stages}
        # 累积基线：进入某阶段时已完成的百分比
        baseline = 0.0
        self._stage_baseline: dict[str, float] = {}
        for s in self._stages:
            self._stage_baseline[s.name] = baseline
            baseline += self._stage_span[s.name]

        self._current_stage: str = ""
        self._stage_started_at: float = 0.0
        self._items_current: int = 0
        self._items_total: int = 0

        self._started_at: float = 0.0
        self._finished_at: float = 0.0
        self._state: str = "idle"

        # EMA 速率采样：存最近 N 次 (percent, time)
        self._samples: list[tuple[float, float]] = []
        self._ema_rate_percent_per_sec: float = 0.0  # % 每秒

        self.last_event: TaskProgressEvent | None = None

    # ── 状态流转 ─────────────────────────────────────────
    def start(self) -> TaskProgressEvent:
        self._started_at = time.time()
        self._state = "running"
        return self._emit(self._build_event(0.0))

    def finish(self) -> TaskProgressEvent:
        self._finished_at = time.time()
        self._state = "finished"
        self._current_stage = ""
        return self._emit(self._build_event(100.0, state="finished"))

    def cancel(self) -> TaskProgressEvent:
        self._state = "cancelled"
        return self._emit(self._build_event(self._overall_percent(), state="cancelled"))

    def fail(self, message: str = "") -> TaskProgressEvent:
        self._state = "failed"
        ev = self._build_event(self._overall_percent(), state="failed")
        ev.message = message
        return self._emit(ev)

    def pause(self) -> TaskProgressEvent:
        if self._state == "running":
            self._state = "paused"
        return self._emit(self._build_event(self._overall_percent(), state="paused"))

    def resume(self) -> TaskProgressEvent:
        if self._state == "paused":
            self._state = "running"
        return self._emit(self._build_event(self._overall_percent(), state="running"))

    # ── 阶段级控制 ────────────────────────────────────────
    def begin_stage(self, stage_name: str, *, expected_items: int = 0) -> TaskProgressEvent:
        spec = self._get_spec(stage_name)
        self._current_stage = stage_name
        self._stage_started_at = time.time()
        self._items_current = 0
        self._items_total = max(expected_items, spec.expected_items) if spec else expected_items
        ev = self._build_event(self._overall_percent())
        if spec and spec.display_name:
            ev.message = spec.display_name
        return self._emit(ev)

    def end_stage(self, stage_name: str) -> TaskProgressEvent:
        span = self._stage_span.get(stage_name, 0.0)
        baseline = self._stage_baseline.get(stage_name, 0.0)
        # 阶段结束时强制推进到该阶段 100%，避免子项偏差累积
        return self._emit(self._build_event(baseline + span))

    # ── 子项级控制 ────────────────────────────────────────
    def advance_item(self, *, by: int = 1, item_total_override: int = 0) -> TaskProgressEvent:
        """阶段内子项前进（例如处理完一份文档）。"""
        if item_total_override > 0:
            self._items_total = item_total_override
        self._items_current = max(self._items_current, min(self._items_current + by, self._items_total))
        return self._emit(self._build_event(self._overall_percent()))

    def set_item_progress(self, current: int, total: int | None = None) -> TaskProgressEvent:
        if total is not None and total > 0:
            self._items_total = total
        self._items_current = max(0, min(current, self._items_total))
        return self._emit(self._build_event(self._overall_percent()))

    # ── 直接设置百分（兼容旧式事件桥接） ──────────────────
    def set_percent(self, percent: float, *, message: str = "") -> TaskProgressEvent:
        ev = self._build_event(max(0.0, min(percent, 100.0)))
        if message:
            ev.message = message
        return self._emit(ev)

    # ── 内部 ──────────────────────────────────────────────
    def _get_spec(self, stage_name: str) -> StageSpec | None:
        for s in self._stages:
            if s.name == stage_name:
                return s
        return None

    def _overall_percent(self) -> float:
        if not self._current_stage:
            # 未进入任何阶段，返回上次累积基线（若有）或 0
            return self._stage_baseline.get(self._stages[-1].name, 0.0) if self._state == "finished" else 0.0
        spec = self._get_spec(self._current_stage)
        baseline = self._stage_baseline.get(self._current_stage, 0.0)
        span = self._stage_span.get(self._current_stage, 0.0)
        if spec and spec.has_items and self._items_total > 0:
            ratio = min(1.0, self._items_current / self._items_total)
        else:
            # 无子项或未知总数：阶段未结束时按 50%（介于开始基线到阶段完成之间）
            # 但实际 end_stage 会强制跳满，这里保持阶段内无信息时只推进到基线
            # 给一个"阶段运行中"视觉提示：基线 + 10%
            ratio = 0.0 if self._state != "running" else 0.1
        return min(100.0, baseline + span * ratio)

    def _update_eta(self, now: float, percent: float) -> tuple[float, float]:
        """返回 (rate_percent_per_sec, eta_seconds)；未达样本量返回 (0, 0)。"""
        # 保留最近采样（最多 32 个）
        self._samples.append((percent, now))
        if len(self._samples) > 32:
            self._samples.pop(0)
        if len(self._samples) < _EMA_WARMUP or self._started_at <= 0:
            return 0.0, 0.0

        # 瞬时速率：相邻样本的斜率
        last_pct, last_ts = self._samples[0]
        inst_rates: list[float] = []
        for pct, ts in self._samples[1:]:
            dt = ts - last_ts
            if dt > 0:
                inst_rates.append((pct - last_pct) / dt)
            last_pct, last_ts = pct, ts
        if not inst_rates:
            return 0.0, 0.0
        # EMA 平滑瞬时速率（按序列顺序逐次应用 α）
        smoothed = inst_rates[0]
        for r in inst_rates[1:]:
            smoothed = _EMA_ALPHA * r + (1 - _EMA_ALPHA) * smoothed
        smoothed = max(smoothed, 1e-9)  # 防除零
        remaining_pct = max(0.0, 100.0 - percent)
        eta = remaining_pct / smoothed
        if not math.isfinite(eta):
            eta = 0.0
        return smoothed, eta

    def _build_event(self, percent: float, *, state: str | None = None) -> TaskProgressEvent:
        now = time.time()
        rate, eta = self._update_eta(now, percent)
        spec = self._get_spec(self._current_stage) if self._current_stage else None
        display_stage = ""
        if spec:
            display_stage = spec.display_name or spec.name
        ev = TaskProgressEvent(
            task_id=self._task_id,
            stage=self._current_stage,
            display_stage=display_stage,
            percent=round(percent, 3),
            state=state or self._state,
            item_current=self._items_current,
            item_total=self._items_total,
            rate=round(rate, 6),
            rate_unit="%/s",
            eta_seconds=round(eta, 1),
            started_at=self._started_at,
            updated_at=now,
        )
        return ev

    def _emit(self, ev: TaskProgressEvent) -> TaskProgressEvent:
        self.last_event = ev
        if self._on_event is not None:
            try:
                self._on_event(ev)
            except Exception:  # noqa: BLE001 — 回调失败不得影响 tracker
                pass
        return ev


# ── 辅助：旧式信号 → 新事件桥接 ─────────────────────────────


def bridge_percent_to_event(
    percent: int,
    *,
    task_id: str = "",
    stage: str = "",
    display_stage: str = "",
) -> TaskProgressEvent:
    """旧式 ``progress(int)`` 信号到统一事件的轻量桥接。"""
    return TaskProgressEvent(
        task_id=task_id,
        stage=stage,
        display_stage=display_stage,
        percent=float(percent),
        state="running",
        started_at=0,
        updated_at=time.time(),
    )


def bridge_items_to_event(
    current: int,
    total: int,
    *,
    task_id: str = "",
    stage: str = "",
    display_stage: str = "",
    stage_baseline_pct: float = 0.0,
    stage_span_pct: float = 100.0,
) -> TaskProgressEvent:
    """旧式 ``document_progress(processed, total)`` → 统一事件。"""
    ratio = 0.0 if total <= 0 else min(1.0, current / total)
    pct = stage_baseline_pct + stage_span_pct * ratio
    return TaskProgressEvent(
        task_id=task_id,
        stage=stage,
        display_stage=display_stage,
        percent=round(pct, 3),
        state="running",
        item_current=current,
        item_total=total,
        started_at=0,
        updated_at=time.time(),
    )


# ── 辅助：把统一事件转回 GUI 所需的旧式信号值（方便逐步迁移）──────


def event_to_percent(ev: TaskProgressEvent) -> int:
    return max(0, min(100, int(round(ev.percent))))


def event_to_stage_label(ev: TaskProgressEvent, *, default: str = "") -> str:
    """生成中文阶段标签，含子项与 ETA。"""
    if ev.state == "finished":
        return "✓ " + (ev.display_stage or _("全部完成"))
    if ev.state == "failed":
        return "✗ " + (ev.message or _("失败"))
    if ev.state == "cancelled":
        return _("已取消")
    parts: list[str] = []
    if ev.display_stage:
        parts.append(ev.display_stage)
    if ev.item_total > 0:
        parts.append(_("{0}/{1}").format(ev.item_current, ev.item_total))
    if ev.eta_seconds > 0:
        parts.append(_("剩余 {0}").format(format_eta(ev.eta_seconds)))
    if ev.message and ev.message not in parts:
        parts.append(ev.message)
    return " · ".join(parts) if parts else default or ev.stage


def format_eta(seconds: float) -> str:
    """秒数 → 「12分30秒」「1小时20分」紧凑格式。"""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return _("{0}秒").format(seconds)
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return _("{0}分{1}秒").format(minutes, secs) if secs else _("{0}分").format(minutes)
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return _("{0}小时{1}分").format(hours, minutes) if minutes else _("{0}小时").format(hours)
    days, hours = divmod(hours, 24)
    return _("{0}天{1}小时").format(days, hours) if hours else _("{0}天").format(days)


def _(text: str) -> str:
    """延迟导入 i18n，避免 import services.progress 时 GUI 环境未就绪。"""
    try:
        from ..gui.i18n import _ as __translate  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return text
    return __translate(text)
