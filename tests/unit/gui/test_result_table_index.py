"""GUI 结果表的「打开大 CSV」索引判据（2026-11-27）。

## 背景

`CsvIndexWorker` 在后台线程为 CSV 建分页索引：一次遍历同时拿到表头、总行数、
每页起始字节偏移。偏移表让 `_load_page` 直接 `seek`，翻页从「与页码线性相关」
变成「与页码无关」。

## 这些用例守什么

★ 这三条守的是**索引的正确性**，不是性能数字：
偏移算早一行 ⇒ 用户看到重复行；算晚一行 ⇒ 缺行。两者都不报错，只静静地错。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtCore")

from omnicrawler.gui.core.workers import ROWS_PER_PAGE, CsvIndexWorker  # noqa: E402


def _write_csv(path: Path, rows: int, columns: int = 4) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([f"c{index}" for index in range(columns)])
        for index in range(rows):
            writer.writerow([f"r{index}c{column}" for column in range(columns)])


def test_index_records_a_byte_offset_per_page(tmp_path: Path) -> None:
    """★ 偏移表必须**每页一条**，否则 `_load_page` 只能退回逐行跳过。"""
    path = tmp_path / "data.csv"
    total = ROWS_PER_PAGE * 3 + 7
    _write_csv(path, total)
    headers, rows, _size, offsets = CsvIndexWorker(path).work()
    assert headers == ["c0", "c1", "c2", "c3"], headers
    assert rows == total
    # 表头之后是第 0 页，然后每满 ROWS_PER_PAGE 行再记一个
    assert len(offsets) == 4, offsets
    assert offsets == sorted(offsets), "偏移必须单调递增"


def test_every_offset_lands_on_a_page_boundary(tmp_path: Path) -> None:
    """★ 偏移的**正确性**：从每个偏移 seek 过去，读到的必须正好是那一页的第一行。

    这条防的是「偏移算早/算晚一行」这类静默错位 —— 它不会报错，只会让用户看到重复行。
    """
    path = tmp_path / "data.csv"
    total = ROWS_PER_PAGE * 3 + 7
    _write_csv(path, total)
    _headers, _rows, _size, offsets = CsvIndexWorker(path).work()
    for page, offset in enumerate(offsets):
        with path.open("rb") as raw:
            raw.seek(offset)
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            first = next(csv.reader(text), None)
        assert first, f"第 {page} 页偏移 {offset} 处读不到任何内容"
        expected = f"r{page * ROWS_PER_PAGE}c0"
        assert first[0] == expected, (page, offset, first[0], expected)


def test_index_cost_is_proportional_to_pages_not_rows(tmp_path: Path) -> None:
    """内存形态：两万行 CSV 的索引应是「每页一个整数」，不是「每行一个整数」。

    ★ 判据用**偏移表长度**而不是 process RSS：后者受解释器/库抖动影响，
      在 CI 上会假红。
    """
    path = tmp_path / "data.csv"
    total = 20_000
    _write_csv(path, total, columns=3)
    size_mb = path.stat().st_size / (1024 * 1024)
    _headers, rows, _size, offsets = CsvIndexWorker(path).work()
    assert rows == total
    assert len(offsets) == (total // ROWS_PER_PAGE) + 1, len(offsets)
    assert len(offsets) < total / 100, "索引条目数与行数同阶 ⇒ 可能把每行都记下来了"
    assert size_mb > 0.1, "夹具太小，这条守卫没有意义"


def test_max_rows_stops_the_scan_early(tmp_path: Path) -> None:
    """`max_rows` 提前停止扫描的能力不能被偏移表改坏。"""
    path = tmp_path / "data.csv"
    _write_csv(path, 500)
    _headers, rows, _size, offsets = CsvIndexWorker(path, max_rows=100).work()
    assert rows == 100, rows
    assert offsets, "提前停止时仍要给第一页偏移"
