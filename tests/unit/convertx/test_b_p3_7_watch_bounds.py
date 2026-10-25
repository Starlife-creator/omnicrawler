"""B-P3-7：目录监听成功签名 LRU 容量淘汰；失败签名单独短重试窗口。"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import omnicrawler.convertx.watch as watch_mod
from omnicrawler.convertx.watch import FolderWatcher, WatchConfig


@dataclass
class _FakeResult:
    output_path: Path
    status: str = "converted"
    warnings: tuple[str, ...] = ()


def _settled(path: Path) -> None:
    past = time.time() - 60
    os.utime(path, (past, past))


def test_failed_signature_retried_after_retry_window(tmp_path: Path) -> None:
    source = tmp_path / "inbox"
    source.mkdir()
    target = tmp_path / "out"
    incoming = source / "a.csv"
    incoming.write_text("x", encoding="utf-8")
    _settled(incoming)

    calls = {"n": 0}

    def converter(path: Path, target_path: Path, rule: object) -> _FakeResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return _FakeResult(target_path)

    watcher = FolderWatcher(
        WatchConfig(source, target, ".jsonl", settle_seconds=0), converter=converter
    )
    t0 = time.time()
    first = watcher.scan_once(now=t0)
    assert len(first) == 1 and first[0].status == "error"

    # 短重试窗口内：失败签名跳过，不重复打扰
    assert watcher.scan_once(now=t0 + 100) == []

    # 窗口过期后：自动重试并成功
    third = watcher.scan_once(now=t0 + watch_mod._FAILED_RETRY_SECONDS + 1)
    assert len(third) == 1 and third[0].status == "converted"
    assert calls["n"] == 2


def test_success_signature_table_evicts_oldest_when_capped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(watch_mod, "_MAX_PROCESSED", 3)
    source = tmp_path / "inbox"
    source.mkdir()
    target = tmp_path / "out"
    for index in range(5):
        path = source / f"a{index}.csv"
        path.write_text(str(index), encoding="utf-8")
        _settled(path)

    def converter(path: Path, target_path: Path, rule: object) -> _FakeResult:
        return _FakeResult(target_path)

    watcher = FolderWatcher(
        WatchConfig(source, target, ".jsonl", settle_seconds=0), converter=converter
    )
    events = watcher.scan_once()
    assert len(events) == 5
    # 成功签名表受容量上限约束，长期运行不再无界增长
    assert len(watcher._processed) <= 3
    # 失败表为空：成功条目不进入失败窗口
    assert watcher._failed == {}
