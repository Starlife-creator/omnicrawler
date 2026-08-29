"""Regression tests for bounded PDF pipeline scheduling and exact failure state."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawler.pdfx.concurrency import iter_bounded_futures
from omnicrawler.pdfx.database import Database


def test_bounded_futures_latches_stop_without_consuming_tail() -> None:
    submitted: list[int] = []
    source_closed = False

    def source():
        nonlocal source_closed
        try:
            yield from range(100)
        finally:
            source_closed = True

    def submit(item: int) -> concurrent.futures.Future[int]:
        submitted.append(item)
        future: concurrent.futures.Future[int] = concurrent.futures.Future()
        future.set_result(item * 2)
        return future

    completed = list(
        iter_bounded_futures(
            source(),
            submit,
            max_in_flight=8,
            should_stop=lambda: len(submitted) >= 3,
        )
    )

    assert submitted == [0, 1, 2]
    assert sorted(future.result() for future, _item in completed) == [0, 2, 4]
    assert source_closed is True


def test_database_iter_rows_stream_survives_writes(tmp_path: Path) -> None:
    db = Database(tmp_path / "stream.sqlite3")
    try:
        with db.transaction() as connection:
            for index in range(20):
                connection.execute(
                    """INSERT INTO documents(
                           doc_id, sha256, primary_path, filename, size_bytes,
                           status, created_at, updated_at
                       ) VALUES(?, ?, ?, ?, 1, 'ingested', 't', 't')""",
                    (f"d{index:02d}", f"h{index:02d}", f"/{index}.pdf", f"{index:02d}.pdf"),
                )

        rows = db.iter_rows(
            "SELECT doc_id FROM documents ORDER BY doc_id", fetch_size=3,
        )
        first = next(rows)
        db.execute(
            "UPDATE documents SET status='parsed' WHERE doc_id=?", (first["doc_id"],),
        )
        assert first["doc_id"] == "d00"
        assert len(list(rows)) == 19
    finally:
        db.close()


def test_ocr_pool_crash_marks_exact_unfinished_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures.process import BrokenProcessPool

    from omnicrawler.pdfx import ocr

    rows = [
        {"doc_id": f"doc{page}", "page_no": page, "primary_path": f"/{page}.pdf"}
        for page in range(1, 5)
    ]

    class FakeDb:
        def __init__(self) -> None:
            self.errors: list[str] = []
            self.ops: list[tuple[str, tuple]] = []

        def fetchall(self, _sql, _params=()):
            return rows

        def fetchone(self, _sql, _params=()):
            return {"n": 1}

        def execute(self, sql, params=()):
            self.ops.append((sql, params))

        def add_error(self, doc_id, _stage, _exc):
            self.errors.append(doc_id)

    class FakePool:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def submit(self, _function, args):
            _path, page_no, _dpi = args
            future = concurrent.futures.Future()
            future.set_result(("worker-id", page_no, f"text-{page_no}", 0.9, 6, 0.0, None))
            return future

    def finish_second_then_crash(items, submit, **_kwargs):
        batch = list(items)
        futures = [submit(item) for item in batch]
        yield futures[1], batch[1]
        raise BrokenProcessPool("worker died after out-of-order completion")

    monkeypatch.setattr(ocr, "adaptive_ocr_workers", lambda _workers: 2)
    monkeypatch.setattr(ocr, "create_backend", lambda _config: SimpleNamespace())
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", FakePool)
    monkeypatch.setattr(ocr, "iter_bounded_futures", finish_second_then_crash)

    db = FakeDb()
    summary = ocr.ocr_stage(
        SimpleNamespace(ocr={"backend": "paddle", "dpi": 150}), db, ocr_workers=2,
    )

    assert summary["recognized"] == 1
    assert summary["skipped"] == 3
    assert set(db.errors) == {"doc1", "doc3", "doc4"}
    assert "doc2" not in db.errors
