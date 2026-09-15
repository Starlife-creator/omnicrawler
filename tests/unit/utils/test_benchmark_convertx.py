from __future__ import annotations

import csv
import importlib.util
import json
import os

import pytest

from tools import benchmark_convertx

#: parquet 用例需要**可选依赖** pyarrow（`omnicrawler[storage]`）。
#:
#: **CI 里缺它必须报错，不许跳过**（2026-09-15 实测）：CI 的 test job 曾经装了 duckdb 却没装 pyarrow，
#: 于是这条用例在 ubuntu/macOS 上失败；若这里改成"缺了就 skip"，就等于用 skip 把它藏起来
#: （同一次 CI 里 GUI 用例没有豁免所以红了，这个缺陷才被看见）。参考环境必须真跑。
if importlib.util.find_spec("pyarrow") is None:
    if os.environ.get("CI"):
        pytest.fail(
            "CI 缺少 pyarrow：parquet 转换用例不得在参考环境里被跳过。"
            "请在 quality.yml 的 test job 安装 'omnicrawler[storage]'。",
            pytrace=False,
        )
    pytest.skip(
        "parquet 用例需要可选依赖 pyarrow：pip install 'omnicrawler[storage]'",
        allow_module_level=True,
    )


@pytest.mark.parametrize("case", benchmark_convertx.CASES)
def test_worker_measures_and_validates_conversion(case, tmp_path):
    source_format, target_format = benchmark_convertx.CASE_FORMATS[case]
    source = tmp_path / f"input.{source_format}"
    target = tmp_path / f"output.{target_format}"
    benchmark_convertx.generate_fixture(source, format_name=source_format, rows=25)

    sample = benchmark_convertx.run_worker(case, source, target, 25)

    assert sample["rows"] == 25
    assert sample["duration_seconds"] >= 0
    assert sample["input_bytes"] > 0
    assert sample["output_bytes"] > 0
    assert sample["correctness"] == {
        "rows": 25,
        "first_and_last_match": True,
        "late_field_preserved": True,
    }


def test_suite_uses_fresh_processes_and_writes_machine_readable_report(tmp_path):
    payload = benchmark_convertx.run_suite(sizes=[12], repeats=1, work_dir=tmp_path)

    assert payload["schema_version"] == 1
    assert payload["run_type"] == "fresh-process"
    assert payload["parameters"] == {
        "sizes": [12],
        "repeats": 1,
        "cases": [
            "csv-jsonl", "csv-jsonl-auto", "jsonl-csv", "jsonl-jsonl", "jsonl-xlsx", "xlsx-jsonl",
            "jsonl-parquet", "jsonl-duckdb",
            "parquet-jsonl",
            "duckdb-jsonl",
        ],
    }
    assert {(sample["case"], sample["rows"]) for sample in payload["samples"]} == {
        ("csv-jsonl", 12),
        ("csv-jsonl-auto", 12),
        ("jsonl-csv", 12),
        ("jsonl-jsonl", 12),
        ("jsonl-xlsx", 12),
        ("xlsx-jsonl", 12),
        ("jsonl-parquet", 12),
        ("jsonl-duckdb", 12),
        ("parquet-jsonl", 12),
        ("duckdb-jsonl", 12),
    }
    assert all(sample["peak_rss_bytes"] is None or sample["peak_rss_bytes"] > 0 for sample in payload["samples"])


def test_fixture_covers_multiline_csv_and_late_field(tmp_path):
    path = tmp_path / "input.csv"
    benchmark_convertx.generate_fixture(path, format_name="csv", rows=998)
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["note"] == "line one\nline two 0"
    assert rows[-1]["note"] == "line one\nline two 997"
    assert rows[-1]["tail_only"] == "present"

    jsonl = tmp_path / "input.jsonl"
    benchmark_convertx.generate_fixture(jsonl, format_name="jsonl", rows=2)
    values = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
    assert values[-1]["tail_only"] == "present"
