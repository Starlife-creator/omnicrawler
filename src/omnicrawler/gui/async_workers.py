"""异步工作线程模块。

将数据密集操作（CSV 加载、JSONL 解析、SQLite 查询、模板组合）
移出 GUI 主线程，避免界面冻结。

使用 QThread + Signal 模式，结果在主线程中安全消费。
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal


class CsvLoadWorker(QThread):
    """异步 CSV 加载线程。

    读取 CSV 文件并返回 (headers, rows, field_completeness) 元组。
    支持大文件抽样限制，避免内存溢出。
    """

    finished_loading = Signal(list, list, dict, int)  # headers, sample_rows, completeness, total_rows
    failed = Signal(str)

    def __init__(
        self,
        path: str | Path,
        *,
        sample_limit: int = 50_000,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._sample_limit = sample_limit

    def run(self) -> None:
        try:
            with self._path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = list(reader.fieldnames or [])
                present = {name: 0 for name in headers}
                sample_rows: list[dict[str, str]] = []
                total = 0

                for row in reader:
                    if self.isInterruptionRequested():
                        return
                    total += 1
                    for name in headers:
                        if str(row.get(name, "")).strip():
                            present[name] += 1
                    if total <= self._sample_limit:
                        sample_rows.append(dict(row))

            self.finished_loading.emit(headers, sample_rows, present, total)
        except (OSError, UnicodeError, csv.Error) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class JsonlLoadWorker(QThread):
    """异步 JSONL 加载线程。

    逐行解析 JSONL 文件，返回解析后的记录列表。
    """

    finished_loading = Signal(list, int)  # records, total_count
    failed = Signal(str)

    def __init__(
        self,
        path: str | Path,
        *,
        sample_limit: int = 10_000,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._sample_limit = sample_limit

    def run(self) -> None:
        try:
            records: list[dict[str, Any]] = []
            total = 0
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if self.isInterruptionRequested():
                        return
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    if total <= self._sample_limit:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            self.finished_loading.emit(records, total)
        except (OSError, UnicodeError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class SqliteQueryWorker(QThread):
    """异步 SQLite 查询线程。

    在后台线程执行 SQLite 查询，返回列名和行数据。
    """

    finished_query = Signal(list, list)  # column_names, rows
    failed = Signal(str)

    def __init__(
        self,
        db_path: str | Path,
        query: str,
        *,
        params: tuple = (),
        row_limit: int = 10_000,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._db_path = Path(db_path)
        self._query = query
        self._params = params
        self._row_limit = row_limit

    def run(self) -> None:
        try:
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=5.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(self._query, self._params)
            column_names = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = []
            for i, row in enumerate(cursor):
                if self.isInterruptionRequested():
                    conn.close()
                    return
                if i >= self._row_limit:
                    break
                rows.append(dict(row))
            conn.close()
            self.finished_query.emit(column_names, rows)
        except sqlite3.Error as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class TemplateCombineWorker(QThread):
    """异步模板组合线程。

    在后台执行模板组合逻辑，避免大量模板处理时阻塞 UI。
    """

    finished_combining = Signal(object)  # combined_config
    failed = Signal(str)

    def __init__(
        self,
        template_loader,
        template_names: list[str],
        *,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._loader = template_loader
        self._names = template_names

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            combined = self._loader.combine(self._names)
            if not self.isInterruptionRequested():
                self.finished_combining.emit(combined)
        except Exception as exc:
            self.failed.emit(str(exc))


class CsvIndexWorker(QThread):
    """异步 CSV 索引线程。

    扫描 CSV 文件建立行偏移索引和表头，供 CsvStreamModel 流式加载使用。
    大文件仅统计前 100000 行防止内存溢出。
    """

    finished_indexing = Signal(list, int, float)  # headers, total_rows, file_size
    failed = Signal(str)

    def __init__(self, path: str | Path, parent=None, *, max_rows: int | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        # S3.1.21：接受 max_rows 参数（调用方传值不再 TypeError）；
        # None 表示完整计数（B9 语义），>0 时提前停止扫描
        self._max_rows = max_rows

    def run(self) -> None:
        try:
            file_size = self._path.stat().st_size
            with self._path.open("r", encoding="utf-8-sig") as handle:
                reader = csv.reader(handle)
                first_row = next(reader, None)
                if first_row is None:
                    self.finished_indexing.emit([], 0, float(file_size))
                    return
                headers = [str(h).strip() for h in first_row]
                # B9：完整计数（不再截断前 100000 行）；内存占用 O(1)
                row_count = 0
                for _ in reader:
                    if self.isInterruptionRequested():
                        return
                    row_count += 1
                    if self._max_rows is not None and row_count >= self._max_rows:
                        break
            self.finished_indexing.emit(headers, row_count, float(file_size))
        except (OSError, UnicodeError, csv.Error) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class JsonlSearchWorker(QThread):
    """异步 JSONL 证据查找线程。

    在 records.jsonl 中查找指定 record_id，返回完整记录。
    """

    found = Signal(str, dict)  # record_id, record
    not_found = Signal(str)  # record_id
    failed = Signal(str)

    def __init__(self, jsonl_path: str | Path, record_id: str, *, parent=None) -> None:
        super().__init__(parent)
        self._path = Path(jsonl_path)
        self._record_id = record_id

    def run(self) -> None:
        try:
            if not self._path.is_file():
                self.not_found.emit(self._record_id)
                return
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if self.isInterruptionRequested():
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        candidate = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(candidate.get("record_id", "")) == self._record_id:
                        self.found.emit(self._record_id, candidate)
                        return
            self.not_found.emit(self._record_id)
        except (OSError, UnicodeError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class AsyncWorkerManager:
    """异步工作线程管理器。

    跟踪活跃的工作线程，确保线程在 widget 销毁时被清理。
    提供便捷方法启动各类异步操作。
    """

    def __init__(self) -> None:
        self._active_workers: list[QThread] = []

    def load_csv(
        self,
        path: str | Path,
        on_finished,
        on_failed=None,
        *,
        sample_limit: int = 50_000,
        parent=None,
    ) -> CsvLoadWorker:
        """启动异步 CSV 加载。"""
        worker = CsvLoadWorker(path, sample_limit=sample_limit, parent=parent)
        worker.finished_loading.connect(on_finished)
        if on_failed:
            worker.failed.connect(on_failed)
        worker.finished.connect(lambda *_: self._cleanup(worker))
        worker.failed.connect(lambda *_: self._cleanup(worker))
        self._active_workers.append(worker)
        worker.start()
        return worker

    def load_jsonl(
        self,
        path: str | Path,
        on_finished,
        on_failed=None,
        *,
        sample_limit: int = 10_000,
        parent=None,
    ) -> JsonlLoadWorker:
        """启动异步 JSONL 加载。"""
        worker = JsonlLoadWorker(path, sample_limit=sample_limit, parent=parent)
        worker.finished_loading.connect(on_finished)
        if on_failed:
            worker.failed.connect(on_failed)
        worker.finished.connect(lambda *_: self._cleanup(worker))
        worker.failed.connect(lambda *_: self._cleanup(worker))
        self._active_workers.append(worker)
        worker.start()
        return worker

    def query_sqlite(
        self,
        db_path: str | Path,
        query: str,
        on_finished,
        on_failed=None,
        *,
        params: tuple = (),
        row_limit: int = 10_000,
        parent=None,
    ) -> SqliteQueryWorker:
        """启动异步 SQLite 查询。"""
        worker = SqliteQueryWorker(
            db_path, query, params=params, row_limit=row_limit, parent=parent,
        )
        worker.finished_query.connect(on_finished)
        if on_failed:
            worker.failed.connect(on_failed)
        worker.finished.connect(lambda *_: self._cleanup(worker))
        worker.failed.connect(lambda *_: self._cleanup(worker))
        self._active_workers.append(worker)
        worker.start()
        return worker

    def index_csv(
        self,
        path: str | Path,
        on_finished,
        on_failed=None,
        *,
        max_rows: int = 100_000,
        parent=None,
    ) -> CsvIndexWorker:
        """启动异步 CSV 索引（行计数 + 表头）。"""
        worker = CsvIndexWorker(path, max_rows=max_rows, parent=parent)
        worker.finished_indexing.connect(on_finished)
        if on_failed:
            worker.failed.connect(on_failed)
        worker.finished.connect(lambda *_: self._cleanup(worker))
        worker.failed.connect(lambda *_: self._cleanup(worker))
        self._active_workers.append(worker)
        worker.start()
        return worker

    def search_jsonl(
        self,
        jsonl_path: str | Path,
        record_id: str,
        on_found=None,
        on_not_found=None,
        on_failed=None,
        *,
        parent=None,
    ) -> JsonlSearchWorker:
        """启动异步 JSONL 证据查找。"""
        worker = JsonlSearchWorker(jsonl_path, record_id, parent=parent)
        if on_found:
            worker.found.connect(on_found)
        if on_not_found:
            worker.not_found.connect(on_not_found)
        if on_failed:
            worker.failed.connect(on_failed)
        worker.finished.connect(lambda *_: self._cleanup(worker))
        worker.failed.connect(lambda *_: self._cleanup(worker))
        self._active_workers.append(worker)
        worker.start()
        return worker

    def _cleanup(self, worker: QThread) -> None:
        """清理已完成的工作线程。"""
        try:
            self._active_workers.remove(worker)
        except ValueError:
            pass

    def cancel_all(self, timeout_ms: int = 3000) -> tuple[QThread, ...]:
        """请求所有工作线程中断，并返回未能在时限内停止的线程。"""
        for worker in list(self._active_workers):
            worker.requestInterruption()
            worker.quit()
        remaining: list[QThread] = []
        for worker in list(self._active_workers):
            if not worker.wait(timeout_ms):
                remaining.append(worker)
        self._active_workers = remaining
        return tuple(remaining)
