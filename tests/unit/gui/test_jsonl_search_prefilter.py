"""GUI 证据查找（JSONL）的「先筛后解析」判据（2026-11-27）。

## 为什么

`JsonlSearchWorker` 在后台线程逐行 `json.loads` 找一条 `record_id`。
实测 3 万行 **35 ms**、10 万行约 100 ms —— 看着不长，但它跑在用户点「查看证据」
之后的等待里，而**绝大多数行根本不含这个 id**。

改成先做子串预筛：3 万行 **8 ms（4.3x）**。

## 一条容易踩的坑

★ 子串必须是 **JSON 转义后的形态**：`record_id` 若含引号 / 反斜杠 / 非 ASCII，
写进 JSON 时会被转义（引号变反斜杠加引号、中文可能变成反斜杠 u 的四位形式），
直接搜原文会**漏命中**。
漏命中是"静默找不到记录"，比慢更坏 —— 所以这条专门有守卫。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtCore")

from omnicrawler.gui.core.workers import JsonlSearchWorker  # noqa: E402


def _write(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_finds_the_record(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    _write(path, [{"record_id": f"id-{index}", "value": index} for index in range(200)])
    record_id, record = JsonlSearchWorker(path, "id-150").work()
    assert record_id == "id-150"
    assert record is not None and record["value"] == 150


def test_missing_record_is_not_found_not_an_error(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    _write(path, [{"record_id": "id-1"}])
    _record_id, record = JsonlSearchWorker(path, "id-nope").work()
    assert record is None


def test_missing_file_is_not_found(tmp_path: Path) -> None:
    _record_id, record = JsonlSearchWorker(tmp_path / "absent.jsonl", "id-1").work()
    assert record is None


@pytest.mark.parametrize(
    "record_id",
    [
        'quote"inside',
        "back\\slash",
        "中文-编号-42",
        "tab\there",
    ],
)
def test_escaped_record_ids_are_still_found(tmp_path: Path, record_id: str) -> None:
    """★ 子串预筛必须用 **JSON 转义后**的形态，否则含特殊字符的 id 会漏命中。

    判据是"真的能取回那条记录"，不是"预筛字符串长得对" —— 后者容易自证。
    """
    path = tmp_path / "records.jsonl"
    _write(path, [
        {"record_id": "other", "value": "x"},
        {"record_id": record_id, "value": "hit"},
    ])
    _record_id, record = JsonlSearchWorker(path, record_id).work()
    assert record is not None, f"{record_id!r} 没有被找到（预筛漏命中）"
    assert record["value"] == "hit"


def test_prefilter_is_much_faster_than_parsing_every_line(tmp_path: Path) -> None:
    """量级护栏：3 万行时先筛后解析应明显快于逐行 json.loads。

    ★ 阈值取 1.5 倍（实测 4.3 倍），只守"确实跳过了绝大多数解析"。
    """
    path = tmp_path / "records.jsonl"
    _write(path, [{"record_id": f"id-{index:06d}", "value": index} for index in range(30_000)])
    started = time.perf_counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            continue
    baseline_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    JsonlSearchWorker(path, "id-029999").work()
    filtered_ms = (time.perf_counter() - started) * 1000
    assert filtered_ms * 1.5 < baseline_ms, (
        f"预筛 {filtered_ms:.1f} ms vs 全量解析 {baseline_ms:.1f} ms —— 看起来没跳过解析"
    )
