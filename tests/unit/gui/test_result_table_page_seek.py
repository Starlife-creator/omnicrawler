"""翻页必须走字节偏移 seek，且不重复量列宽。

为什么要有这个文件（2026-11-25 实测，10 万行 CSV）：

翻页原本有两个开销，都在 **GUI 主线程**：

| 开销 | 实测 |
|---|---|
| `_load_page` 从文件头逐行 skip 到目标页 | 第 10 页 3.8 ms / 第 99 页 35.7 ms |
| `resizeColumnsToContents()` 扫当前页 1000 行算列宽 | 67.4 ms/次 |

合计翻到第 100 页 106.7 ms，几乎全是这两项。

修法：

1. `CsvIndexWorker` 在**后台线程**建「每页起始字节偏移」表（与已有的一次全扫同一趟）；
2. `_load_page` 改用 `seek(offsets[page])`；
3. 列宽每份文件只量一次（列宽与页码无关）。

结果：第 2 页 71 ms -> 6.7 ms，第 100 页 106.7 ms -> 3.9 ms。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicrawler.gui.views.result_table import ROWS_PER_PAGE, CsvStreamModel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _write_csv(path: Path, rows: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["idx", "val"])
        for i in range(rows):
            writer.writerow([i, f"v{i}"])


def test_seek_matches_linear_skip(tmp_path: Path, qapp) -> None:
    """用字节偏移 seek 出来的页，必须与逐行跳过得到的**完全一致**。"""
    path = tmp_path / "data.csv"
    total = ROWS_PER_PAGE * 3 + 5
    _write_csv(path, total)

    model = CsvStreamModel()
    assert model.load_file(path)
    assert len(model._byte_offsets) >= 2, "没有建出字节偏移表"

    for page in range(model.total_pages):
        model.go_to_page(page)
        seeked = [list(row) for row in model._rows]
        # 逐行跳过的参考实现
        expected: list[list[str]] = []
        with path.open(encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            for _ in range(page * ROWS_PER_PAGE):
                next(reader, None)
            for row in reader:
                expected.append([str(c).strip() for c in row])
                if len(expected) >= ROWS_PER_PAGE:
                    break
        assert seeked == expected, f"第 {page} 页内容与逐行跳过不一致"


def test_byte_offsets_cover_every_page(tmp_path: Path, qapp) -> None:
    """偏移表必须覆盖所有页，否则后面的页会退回逐行跳过。"""
    path = tmp_path / "data.csv"
    total = ROWS_PER_PAGE * 4 + 1
    _write_csv(path, total)
    model = CsvStreamModel()
    assert model.load_file(path)
    assert len(model._byte_offsets) == model.total_pages, (
        f"偏移表 {len(model._byte_offsets)} 项，但共 {model.total_pages} 页"
    )


def test_offsets_are_ascending(tmp_path: Path, qapp) -> None:
    """偏移必须严格递增 —— 否则 seek 会读到错位的数据。"""
    path = tmp_path / "data.csv"
    _write_csv(path, ROWS_PER_PAGE * 2 + 10)
    model = CsvStreamModel()
    assert model.load_file(path)
    offsets = model._byte_offsets
    assert offsets == sorted(offsets), offsets
    assert len(set(offsets)) == len(offsets), offsets
