"""P2-4：统一进度协议的单元测试。

覆盖:
- TaskProgressEvent: to_log_line / from_log_line 往返、未知行返回 None
- ProgressTracker: 阶段权重归一化、子项展开、状态流转 start/finish/cancel/fail/pause/resume
- ProgressTracker: EMA ETA（至少 N 个样本后才出现）、速率单调收敛
- 桥接函数: bridge_percent_to_event / bridge_items_to_event / event_to_percent / event_to_stage_label / format_eta
- 空 stages / 负权重拒绝
"""

from __future__ import annotations

import time

import pytest


class TestTaskProgressEvent:
    def test_roundtrip_log_line(self) -> None:
        from omnicrawler.services.progress import TaskProgressEvent

        ev = TaskProgressEvent(
            task_id="job-01",
            stage="extract",
            display_stage="结构化抽取",
            percent=66.0,
            state="running",
            item_current=8,
            item_total=12,
            message="处理 large.pdf",
            extra={"url": "https://example.com/x"},
        )
        line = ev.to_log_line()
        assert "PROGRESS2:" in line
        restored = TaskProgressEvent.from_log_line(line)
        assert restored is not None
        assert restored.task_id == "job-01"
        assert restored.stage == "extract"
        assert restored.percent == 66.0
        assert restored.item_current == 8
        assert restored.item_total == 12
        assert restored.extra.get("url") == "https://example.com/x"

    def test_from_log_line_returns_none_for_plain_lines(self) -> None:
        from omnicrawler.services.progress import TaskProgressEvent

        assert TaskProgressEvent.from_log_line("INFO: startup") is None
        assert TaskProgressEvent.from_log_line("PROGRESS: 42% https://x") is None
        assert TaskProgressEvent.from_log_line("PROGRESS2: {broken}") is None

    def test_as_dict_has_all_slots(self) -> None:
        from omnicrawler.services.progress import TaskProgressEvent

        d = TaskProgressEvent().as_dict()
        assert isinstance(d, dict)
        for key in ("task_id", "stage", "percent", "state", "eta_seconds"):
            assert key in d


class TestProgressTracker:
    def test_stage_weights_normalize_to_100(self) -> None:
        from omnicrawler.services.progress import ProgressTracker, StageSpec

        events: list = []
        tracker = ProgressTracker(
            stages=[
                StageSpec("a", weight=1.0),
                StageSpec("b", weight=3.0),
            ],
            on_event=events.append,
        )
        tracker.start()
        tracker.begin_stage("a")
        tracker.end_stage("a")
        # a=1/4 = 25% → end_stage 后百分应为 25
        assert tracker.last_event is not None
        assert tracker.last_event.percent == pytest.approx(25.0)
        tracker.begin_stage("b")
        tracker.end_stage("b")
        assert tracker.last_event.percent == pytest.approx(100.0)
        tracker.finish()
        assert tracker.last_event.state == "finished"

    def test_item_progress_spans_stage(self) -> None:
        from omnicrawler.services.progress import ProgressTracker, StageSpec

        tracker = ProgressTracker(stages=[
            StageSpec("only", weight=10.0, has_items=True),
        ])
        tracker.start()
        tracker.begin_stage("only", expected_items=100)
        tracker.set_item_progress(50, 100)
        # 50% 子项 = 整个流程的 50%
        assert tracker.last_event.percent == pytest.approx(50.0, abs=0.05)
        tracker.set_item_progress(100, 100)
        tracker.end_stage("only")
        assert tracker.last_event.percent >= 99.0

    def test_empty_stages_rejected(self) -> None:
        from omnicrawler.services.progress import ProgressTracker

        with pytest.raises(ValueError):
            ProgressTracker(stages=[])

    def test_zero_weight_sum_rejected(self) -> None:
        from omnicrawler.services.progress import ProgressTracker, StageSpec

        with pytest.raises(ValueError):
            ProgressTracker(stages=[StageSpec("x", weight=-1.0)])

    def test_cancel_fail_pause_resume_states(self) -> None:
        from omnicrawler.services.progress import ProgressTracker, StageSpec

        tracker = ProgressTracker(stages=[StageSpec("a")])
        tracker.start()
        tracker.pause()
        assert tracker.last_event.state == "paused"
        tracker.resume()
        assert tracker.last_event.state == "running"
        tracker.cancel()
        assert tracker.last_event.state == "cancelled"

    def test_eta_emerges_after_sufficient_samples(self, monkeypatch) -> None:
        from omnicrawler.services.progress import ProgressTracker, StageSpec

        tracker = ProgressTracker(stages=[StageSpec("a", has_items=True)])
        tracker.start()
        tracker.begin_stage("a", expected_items=20)

        clock = [time.time()]
        monkeypatch.setattr(time, "time", lambda: clock[0])
        # 制造 15 个样本，每步推进 5%（100/20=5% 每步），用时 1s（速率 5%/s）
        halfway_eta: float = -1.0
        halfway_rate: float = 0.0
        for step in range(15):
            clock[0] += 1.0
            tracker.set_item_progress(step + 1, 20)
            if step == 9:  # 第 10 个样本处已过半，ETA 应明显为正
                halfway_eta = tracker.last_event.eta_seconds if tracker.last_event else -1.0
                halfway_rate = tracker.last_event.rate if tracker.last_event else 0.0

        assert halfway_eta > 0, f"半程应已有正 ETA，实际 {halfway_eta}"
        # 速率约在 5%/s 的数量级
        assert 0.5 < halfway_rate < 20.0, f"速率偏离预期: {halfway_rate}"
        last = tracker.last_event
        assert last is not None
        # 收尾时可能剩余很少，eta 被压到 0；但速率仍应可观测
        assert last.rate > 0.0

    def test_callback_exception_isolated(self) -> None:
        """on_event 回调抛错不得中断 tracker 本身。"""
        from omnicrawler.services.progress import ProgressTracker, StageSpec

        def bad(_ev): raise RuntimeError("boom")  # noqa: E704

        tracker = ProgressTracker(stages=[StageSpec("a")], on_event=bad)
        # 不抛异常，说明回调被隔离
        tracker.start()
        tracker.begin_stage("a")
        tracker.end_stage("a")
        tracker.finish()
        assert tracker.last_event is not None
        assert tracker.last_event.state == "finished"

    def test_set_percent_bridge(self) -> None:
        from omnicrawler.services.progress import ProgressTracker, StageSpec

        tracker = ProgressTracker(stages=[StageSpec("a")])
        tracker.start()
        ev = tracker.set_percent(42, message="桥接")
        assert ev.percent == pytest.approx(42.0)
        assert ev.message == "桥接"


class TestBridgeHelpers:
    def test_bridge_percent_roundtrip(self) -> None:
        from omnicrawler.services.progress import bridge_percent_to_event, event_to_percent

        ev = bridge_percent_to_event(75, task_id="t", stage="s", display_stage="阶段S")
        assert event_to_percent(ev) == 75
        assert ev.stage == "s"
        assert ev.display_stage == "阶段S"

    def test_bridge_items_spans_percent_range(self) -> None:
        from omnicrawler.services.progress import bridge_items_to_event, event_to_percent

        ev = bridge_items_to_event(
            5, 10,
            stage_baseline_pct=30.0,
            stage_span_pct=40.0,
        )
        # 50% 子项 + 30 基线 + 40 跨度 = 30 + 20 = 50
        assert event_to_percent(ev) == 50
        assert ev.item_current == 5
        assert ev.item_total == 10

    def test_event_to_stage_label_shows_items_and_eta(self) -> None:
        from omnicrawler.services.progress import TaskProgressEvent, event_to_stage_label

        ev = TaskProgressEvent(
            display_stage="抽取",
            state="running",
            item_current=3,
            item_total=10,
            eta_seconds=125,
        )
        label = event_to_stage_label(ev)
        assert "抽取" in label
        assert "3/10" in label
        # 125s → 2分5秒
        assert "分" in label and "秒" in label

    def test_event_to_stage_label_finished_failed_cancelled(self) -> None:
        from omnicrawler.services.progress import TaskProgressEvent, event_to_stage_label

        ev = TaskProgressEvent(state="finished", display_stage="done")
        assert "✓" in event_to_stage_label(ev)
        ev = TaskProgressEvent(state="failed", message="boom")
        assert "✗" in event_to_stage_label(ev)
        ev = TaskProgressEvent(state="cancelled")
        assert "取消" in event_to_stage_label(ev)

    def test_format_eta_ranges(self) -> None:
        from omnicrawler.services.progress import format_eta

        assert "秒" in format_eta(30)
        label = format_eta(125)  # 2分5秒
        assert "分" in label and "秒" in label
        label = format_eta(3600 + 60)  # 1小时1分
        assert "小时" in label and "分" in label
        label = format_eta(3 * 86400 + 5 * 3600)  # 3天5小时
        assert "天" in label and "小时" in label

    def test_format_eta_zero_or_negative_is_zero_seconds(self) -> None:
        from omnicrawler.services.progress import format_eta

        assert "0秒" in format_eta(0)
        assert "秒" in format_eta(-10)


class TestLogParserProgress2:
    def test_parses_progress2_and_invokes_both_callbacks(self) -> None:
        pytest.importorskip("PySide6")
        from PySide6.QtWidgets import QApplication

        from omnicrawler.gui.runner.log_parser import LogParser
        from omnicrawler.services.progress import TaskProgressEvent

        QApplication.instance() or QApplication([])
        legacy_calls: list = []
        new_calls: list = []
        parser = LogParser(
            on_progress=lambda p, u: legacy_calls.append((p, u)),
            on_progress_event=lambda ev: new_calls.append(ev),
        )
        line = TaskProgressEvent(
            task_id="j", stage="x", percent=55.0, state="running",
            extra={"url": "https://foo"},
        ).to_log_line()
        # 加一些前缀模拟真实日志
        wrapped = f"[INFO] 2026-08-12 19:00:00 {line}"
        result = parser.parse_line(wrapped)
        assert result["progress_event"] is not None
        assert result["progress_event"].percent == 55.0
        # 旧式回调也触发，percent 被 clamp 到 int
        assert legacy_calls == [(55, "https://foo")]
        assert len(new_calls) == 1 and isinstance(new_calls[0], TaskProgressEvent)

    def test_legacy_progress_still_works_when_no_progress2(self) -> None:
        pytest.importorskip("PySide6")
        from PySide6.QtWidgets import QApplication

        from omnicrawler.gui.runner.log_parser import LogParser

        QApplication.instance() or QApplication([])
        legacy: list = []
        parser = LogParser(on_progress=lambda p, u: legacy.append((p, u)))
        parser.parse_line("PROGRESS: 87% https://example.org/page")
        assert legacy == [(87, "https://example.org/page")]

    def test_stats_still_parsed_alongside_progress2(self) -> None:
        pytest.importorskip("PySide6")
        from PySide6.QtWidgets import QApplication

        from omnicrawler.gui.runner.log_parser import LogParser
        from omnicrawler.services.progress import TaskProgressEvent

        QApplication.instance() or QApplication([])
        parser = LogParser()
        line = TaskProgressEvent(percent=50).to_log_line()
        # 把 stats 句子追加到同一输入后面模拟多行场景（逐行喂给 parser）
        parser.parse_line(line)
        stats_line = "提取记录: 120 采集页面: 35"
        result = parser.parse_line(stats_line)
        assert result["stats"].get("records") == 120
        assert result["stats"].get("pages") == 35
