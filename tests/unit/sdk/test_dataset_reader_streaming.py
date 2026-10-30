"""A-P3-11：SDK DatasetReader 流式契约回归测试。

- JSONL 逐行迭代，不再整文件 ``read_text().splitlines()``；
- ``iter_artifacts()`` 惰性生成器，``artifacts()`` 保持 tuple 契约；
- 大记录集内存峰值与文件大小解耦（不触发整文件读取路径）。
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from omnicrawler.sdk.data import DatasetReader


def _workspace(tmp_path: Path) -> Path:
    output = tmp_path / "workspace" / "output"
    output.mkdir(parents=True)
    return tmp_path / "workspace"


def test_jsonl_records_stream_line_by_line(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "output" / "records.jsonl").write_text(
        '{"title": "A"}\n\n{"title": "B"}\n[1, 2]\n{"title": "C"}\n',
        encoding="utf-8",
    )
    reader = DatasetReader(workspace)
    records = list(reader.records())
    assert records == [{"title": "A"}, {"title": "B"}, {"title": "C"}]


def test_csv_fallback_streaming(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "output" / "records.csv").write_text(
        "title,url\nAlpha,https://a.example\nBeta,https://b.example\n",
        encoding="utf-8-sig",
    )
    reader = DatasetReader(workspace)
    rows = list(reader.records())
    assert rows == [
        {"title": "Alpha", "url": "https://a.example"},
        {"title": "Beta", "url": "https://b.example"},
    ]


def test_missing_output_yields_nothing(tmp_path: Path) -> None:
    reader = DatasetReader(tmp_path / "empty")
    assert list(reader.records()) == []
    assert reader.artifacts() == ()


def test_records_does_not_read_whole_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """内存契约：records() 路径不得调用整文件 ``read_text``。"""
    workspace = _workspace(tmp_path)
    (workspace / "output" / "records.jsonl").write_text(
        '{"title": "A"}\n{"title": "B"}\n', encoding="utf-8"
    )

    def _read_text(self: Path, *args: object, **kwargs: object) -> str:
        raise AssertionError("records() 不得整文件 read_text（A-P3-11 流式回归）")

    monkeypatch.setattr(Path, "read_text", _read_text)
    reader = DatasetReader(workspace)
    assert [item["title"] for item in reader.records()] == ["A", "B"]


def test_artifacts_keep_tuple_contract_and_sorted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "raw").mkdir()
    (workspace / "artifacts").mkdir()
    (workspace / "raw" / "evidence.bin").write_bytes(b"x")
    (workspace / "artifacts" / "photo.jpg").write_bytes(b"yy")
    reader = DatasetReader(workspace)
    result = reader.artifacts()
    assert isinstance(result, tuple)
    assert [item.name for item in result] == ["photo.jpg", "evidence.bin"]
    assert [item.kind for item in result] == ["attachment", "evidence"]


def test_iter_artifacts_is_lazy_generator(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "output").mkdir(exist_ok=True)
    (workspace / "output" / "report.json").write_text("{}", encoding="utf-8")
    reader = DatasetReader(workspace)
    generator = reader.iter_artifacts()
    assert isinstance(generator, Iterator)
    items = list(generator)
    assert [item.name for item in items] == ["report.json"]
    assert items[0].kind == "output"
