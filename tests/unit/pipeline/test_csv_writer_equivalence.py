"""三个辅助 CSV 的写出：`_write_rows` 必须与 `DictWriter` **逐字节等价**（2026-11-27）。

## 为什么值得换

`record_quality.csv` / `responses.csv` / `errors.csv` 原用 `csv.DictWriter`。
实测（30k 行 × 9 列）：

| 写法 | 耗时 |
|---|---|
| `DictWriter.writerows` | 31.0 ms |
| `csv.writer` + 按序取值 | **14.6 ms** |

## 判据是"逐字节等价"，不是"更快"

换写法最容易悄悄改坏四件事：列顺序、缺失键的默认值、`None` 的处理、
以及值里含引号/逗号/换行的转义。所以这里直接比对**参考实现**的输出字节。
"""

from __future__ import annotations

import csv
import io
from typing import Any

from omnicrawler.pipeline.exporters import _write_rows


def _reference(headers: list[str], rows: list[dict[str, Any]]) -> str:
    """参考实现：就是被替换掉的那段 `DictWriter` 代码。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _actual(headers: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    _write_rows(buffer, headers, rows)
    return buffer.getvalue()


def test_output_is_byte_identical_to_dictwriter() -> None:
    """★ 混入各种会让转义出错的取值：引号、逗号、换行、`None`、缺失键、超长值。"""
    headers = ["record_id", "run_id", "status", "score", "note"]
    rows = [
        {"record_id": "id-1", "run_id": "r", "status": "valid", "score": 0.9, "note": ""},
        {"record_id": 'id-2', "run_id": "r", "status": "valid", "score": None, "note": "a,b"},
        {"record_id": "id-3", "run_id": "r", "status": "review_required", "score": 1,
         "note": 'quote " inside'},
        {"record_id": "id-4", "run_id": "r", "status": "valid", "score": 0.5,
         "note": "line1\nline2"},
        {"record_id": "id-5", "run_id": "r", "status": "valid", "score": 0.5},  # 缺 note
        {"record_id": "id-6", "run_id": "r", "status": "valid", "score": 0.5,
         "note": "x" * 100},
    ]
    assert _actual(headers, rows) == _reference(headers, rows)


def test_missing_key_falls_back_to_empty_like_restval() -> None:
    """★ `DictWriter` 的 `restval` 默认值就是 `""` —— 我们的实现必须一致。"""
    headers = ["a", "b", "c"]
    rows = [{"a": "1"}, {"b": "2"}, {}]
    assert _actual(headers, rows) == _reference(headers, rows)


def test_header_order_follows_the_given_list() -> None:
    """列顺序必须按 `headers`，不是按 dict 插入序。"""
    headers = ["z", "a", "m"]
    rows = [{"a": "A", "m": "M", "z": "Z"}]
    text = _actual(headers, rows)
    assert text.splitlines()[0] == "z,a,m"
    assert text.splitlines()[1] == "Z,A,M"


def test_empty_rows_writes_only_the_header() -> None:
    headers = ["a", "b"]
    assert _actual(headers, []) == _reference(headers, []) == "a,b\r\n"
