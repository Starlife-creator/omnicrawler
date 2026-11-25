"""CSV 写出用 `writer` + 显式列表，但输出必须与 `DictWriter` **逐字节相同**。

为什么要有这个文件（2026-11-25）：

原实现是 `csv.DictWriter` + 生成器 dict：

```python
writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
writer.writeheader()
writer.writerows({key: excel_safe(value) for key, value in row.items()} for row in records)
```

每行都要先造一个 8 键 dict（只为被 DictWriter 再拆开），再走 `_dict_to_list`。
改成按 headers 顺序直接取值：

```python
writer = csv.writer(handle)
writer.writerow(headers)
for row in records:
    writer.writerow([safe(row.get(key, "")) for key in headers])
```

实测 50k 行 × 8 列：123 ms -> 88 ms（1.4x）。

**本文件守的是等价性，不是速度** —— 这类「换个写法更快」的改动最容易悄悄改变
输出（列顺序、缺失值、None 处理）。所以逐字节比对。
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from omnicrawler.core.config import AppConfig
from omnicrawler.core.utils import excel_safe
from omnicrawler.pipeline import exporters
from omnicrawler.state.state_store import StateStore


def _seed(tmp_path: Path) -> tuple[AppConfig, StateStore, str]:
    work = tmp_path / "work"
    work.mkdir()
    raw = {
        "project": {"name": "t", "workspace": str(work)},
        "source": {"kind": "static_html", "seeds": ["https://example.com/l"]},
        "outputs": {"csv": True, "jsonl": False, "xlsx": False, "parquet": False, "duckdb": False},
    }
    config = AppConfig(tmp_path / "c.yaml", tmp_path, raw, work)
    state = StateStore(work / "state.sqlite3")
    run_id = state.start_run("t", "c.yaml")
    # 刻意混入：缺字段、None、超长值、公式前缀、中文、引号、逗号
    rows = []
    payloads = [
        {"title": "正常", "url": "https://e.com/1", "price": 1},
        {"title": None, "url": "https://e.com/2"},
        {"title": "=SUM(A1)", "url": "@user", "price": 3},
        {"title": "a,b\"c", "url": "https://e.com/4", "price": 4},
        {"title": "x" * 40000, "url": "https://e.com/5", "price": 5},
    ]
    for i, payload in enumerate(payloads):
        rows.append((f"r{i}", payload.get("url", ""), "html_item", "2026-01-01",
                     json.dumps(payload, ensure_ascii=False), "{}", run_id, f"fp{i}"))
    with state.conn:
        state.conn.executemany(
            "INSERT INTO records(record_id,source_url,record_type,created_at,"
            "data_json,evidence_json,run_id,request_fingerprint) VALUES(?,?,?,?,?,?,?,?)",
            rows,
        )
    return config, state, run_id


def _dictwriter_reference(headers: list[str], records: list[dict]) -> str:
    """参考实现：原来的 DictWriter + 生成器。"""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({key: excel_safe(value) for key, value in row.items()} for row in records)
    return buffer.getvalue()


def test_csv_output_matches_dictwriter(tmp_path: Path) -> None:
    """实际导出的 records.csv 内容必须与 DictWriter 参考实现逐字节相同。"""
    config, state, run_id = _seed(tmp_path)
    exporters._export_delivery(config, state, run_id)

    csv_path = config.workspace / "output" / "records.csv"
    assert csv_path.is_file(), "没有生成 records.csv"
    # 必须用 newline="" 读：`read_text` 会做通用换行转换（\r\n -> \n），
    # 那样比对的就不是文件真实字节了，而 csv 模块写的是 \r\n。
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        actual = handle.read()

    # 用同样的数据重建参考输出
    raw_rows = state.rows("SELECT * FROM records ORDER BY created_at, record_id", ())
    records: list[dict] = []
    for row in raw_rows:
        flat = {
            "record_id": row["record_id"], "source_url": row["source_url"],
            "record_type": row["record_type"], "created_at": row["created_at"],
        }
        data_flat: dict = {}
        exporters._flatten("", json.loads(row["data_json"]), data_flat)
        for key, value in data_flat.items():
            flat[f"data.{key}" if key in flat else key] = value
        records.append(flat)

    headers = list(records[0]) if records else []
    expected = _dictwriter_reference(headers, records)

    assert actual == expected, (
        "CSV 输出与 DictWriter 参考实现不一致 —— 列顺序/缺失值/None 处理可能被改了"
    )


def test_missing_field_becomes_empty_string(tmp_path: Path) -> None:
    """记录缺少某列时写空串，不是 "None"。"""
    config, state, run_id = _seed(tmp_path)
    exporters._export_delivery(config, state, run_id)
    csv_path = config.workspace / "output" / "records.csv"
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "None" not in text.replace("NoneType", ""), "缺失值被写成了字符串 None"
