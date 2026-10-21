"""watch / preset / 并发闸门测试。"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from omnicrawler.convertx.concurrency import (
    BatchTask,
    EngineGate,
    convert_many,
    engine_key,
)
from omnicrawler.convertx.presets import (
    available_presets,
    builtin_presets,
    get_preset,
    load_presets,
    write_example_presets,
)
from omnicrawler.convertx.watch import FolderWatcher, WatchConfig


def _write_csv(path: Path, text: str = "a,b\n1,2\n") -> Path:
    path.write_text(text, encoding="utf-8-sig")
    return path


def _old_mtime(path: Path) -> None:
    past = time.time() - 60
    os.utime(path, (past, past))


class TestWatch:
    def test_converts_settled_file(self, tmp_path: Path) -> None:
        source = tmp_path / "inbox"
        source.mkdir()
        incoming = _write_csv(source / "a.csv")
        _old_mtime(incoming)
        config = WatchConfig(source, tmp_path / "out", ".jsonl", settle_seconds=0)
        events = FolderWatcher(config).scan_once()
        assert len(events) == 1
        assert events[0].status == "converted"
        assert events[0].output == tmp_path / "out" / "a.jsonl"
        assert events[0].output is not None and events[0].output.is_file()

    def test_ignores_temp_and_hidden_files(self, tmp_path: Path) -> None:
        source = tmp_path / "inbox"
        source.mkdir()
        for name in ("a.csv.part", ".hidden.csv", "~$lock.csv", "b.csv.tmp"):
            path = _write_csv(source / name)
            _old_mtime(path)
        real = _write_csv(source / "real.csv")
        _old_mtime(real)
        config = WatchConfig(source, tmp_path / "out", ".jsonl", settle_seconds=0)
        events = FolderWatcher(config).scan_once()
        assert [event.source.name for event in events] == ["real.csv"]

    def test_does_not_reconvert_unchanged_file(self, tmp_path: Path) -> None:
        source = tmp_path / "inbox"
        source.mkdir()
        incoming = _write_csv(source / "a.csv")
        _old_mtime(incoming)
        watcher = FolderWatcher(WatchConfig(source, tmp_path / "out", ".jsonl", settle_seconds=0))
        assert len(watcher.scan_once()) == 1
        assert watcher.scan_once() == []

    def test_settle_window(self, tmp_path: Path) -> None:
        source = tmp_path / "inbox"
        source.mkdir()
        _write_csv(source / "a.csv")
        watcher = FolderWatcher(WatchConfig(source, tmp_path / "out", ".jsonl", settle_seconds=5))
        assert watcher.scan_once() == []
        future = time.time() + 10
        assert len(watcher.scan_once(now=future)) == 1

    def test_output_subdirectory_is_excluded(self, tmp_path: Path) -> None:
        source = tmp_path / "inbox"
        output = source / "converted"
        output.mkdir(parents=True)
        inside = _write_csv(output / "old.csv")
        _old_mtime(inside)
        incoming = _write_csv(source / "a.csv")
        _old_mtime(incoming)
        config = WatchConfig(source, output, ".jsonl", settle_seconds=0)
        events = FolderWatcher(config).scan_once()
        assert [event.source.name for event in events] == ["a.csv"]

    def test_same_directory_does_not_loop(self, tmp_path: Path) -> None:
        source = tmp_path / "inbox"
        source.mkdir()
        incoming = _write_csv(source / "a.csv")
        _old_mtime(incoming)
        watcher = FolderWatcher(WatchConfig(source, source, ".jsonl", settle_seconds=0))
        first = watcher.scan_once()
        assert len(first) == 1
        assert watcher.scan_once() == []

    def test_error_event_does_not_stop_scan(self, tmp_path: Path) -> None:
        source = tmp_path / "inbox"
        source.mkdir()
        bad = source / "bad.xyz"
        bad.write_text("x", encoding="utf-8")
        _old_mtime(bad)
        good = _write_csv(source / "good.csv")
        _old_mtime(good)
        config = WatchConfig(source, tmp_path / "out", ".jsonl", settle_seconds=0)
        events = FolderWatcher(config).scan_once()
        statuses = {event.source.name: event.status for event in events}
        assert statuses["bad.xyz"] == "error"
        assert statuses["good.csv"] == "converted"

    def test_rejects_read_only_target(self, tmp_path: Path) -> None:
        source = tmp_path / "inbox"
        source.mkdir()
        with pytest.raises(ValueError, match="不可写"):
            WatchConfig(source, tmp_path / "out", ".pptx", settle_seconds=0)

    def test_rejects_unknown_target(self, tmp_path: Path) -> None:
        source = tmp_path / "inbox"
        source.mkdir()
        with pytest.raises(ValueError, match="未知目标格式"):
            WatchConfig(source, tmp_path / "out", ".nope", settle_seconds=0)


class TestPresets:
    def test_builtin_presets_present(self) -> None:
        presets = builtin_presets()
        assert "table-archive" in presets
        assert presets["table-archive"].output_rule["on_conflict"] == "rename"

    def test_load_is_lenient(self, tmp_path: Path) -> None:
        path = tmp_path / "presets.json"
        path.write_text(json.dumps({
            "presets": {
                "mine": {
                    "description": "test",
                    "options": {"quality": 70},
                    "convert": {
                        "compression": "ultra",
                        "on_error": "explode",
                        "unknown_key": 1,
                    },
                    "output": {"on_conflict": "nope", "bogus": True},
                }
            }
        }), encoding="utf-8")
        presets = load_presets(path)
        preset = presets["mine"]
        assert preset.convert_kwargs["compression"] == "zstd"
        assert preset.convert_kwargs["on_error"] == "skip"
        assert preset.output_rule["on_conflict"] == "overwrite"
        assert any("compression" in warning for warning in preset.warnings)
        assert any("unknown_key" in warning for warning in preset.warnings)
        assert any("bogus" in warning for warning in preset.warnings)

    def test_user_preset_overrides_builtin(self, tmp_path: Path) -> None:
        path = tmp_path / "presets.json"
        path.write_text(json.dumps({
            "presets": {"table-archive": {"output": {"on_conflict": "error"}}}
        }), encoding="utf-8")
        preset = get_preset("table-archive", path)
        assert preset.output_rule["on_conflict"] == "error"

    def test_unknown_preset_raises(self) -> None:
        with pytest.raises(KeyError):
            get_preset("does-not-exist")

    def test_example_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "presets.json"
        write_example_presets(path)
        loaded = load_presets(path)
        assert set(loaded) == set(builtin_presets())
        assert available_presets(path)["image-web"].options["quality"] == 82


class TestConcurrency:
    def test_engine_key(self) -> None:
        assert engine_key(".pdf", ".docx") == "pdf"
        assert engine_key(".png", ".webp") == "image"
        assert engine_key(".docx", ".md") == "document"
        assert engine_key(".csv", ".parquet") == "columnar"
        assert engine_key(".csv", ".jsonl") == "table"

    def test_gate_limits_concurrency(self) -> None:
        gate = EngineGate(default_limit=1)
        active = 0
        peak = 0
        lock = threading.Lock()

        def worker() -> None:
            nonlocal active, peak
            with gate.slot("x"):
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.02)
                with lock:
                    active -= 1

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert peak == 1

    def test_convert_many_preserves_order_and_partial_failure(self, tmp_path: Path) -> None:
        good1 = _write_csv(tmp_path / "a.csv")
        good2 = _write_csv(tmp_path / "b.csv")
        bad = tmp_path / "bad.xyz"
        bad.write_text("x", encoding="utf-8")
        tasks = [
            BatchTask(good1, tmp_path / "a.jsonl"),
            BatchTask(bad, tmp_path / "bad.jsonl"),
            BatchTask(good2, tmp_path / "b.jsonl"),
        ]
        outcomes = convert_many(tasks, max_workers=2)
        assert [outcome.status for outcome in outcomes] == ["completed", "failed", "completed"]
        assert outcomes[1].error
        assert (tmp_path / "a.jsonl").is_file()
        assert (tmp_path / "b.jsonl").is_file()

    def test_convert_many_empty(self) -> None:
        assert convert_many([]) == []
