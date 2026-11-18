"""表格数据读写多后端包（CSV / JSONL / Excel / Parquet / DuckDB）。"""

from __future__ import annotations

from ._common import (
    _ALIASES as _ALIASES,
)
from ._common import (
    _BASE_COLUMNS as _BASE_COLUMNS,
)
from ._common import (
    _DECODE_CHUNK_BYTES as _DECODE_CHUNK_BYTES,
)
from ._common import (
    _ENCODING_SAMPLE_BYTES as _ENCODING_SAMPLE_BYTES,
)
from ._common import (
    _IMAGE_ANIMATED as _IMAGE_ANIMATED,
)
from ._common import (
    _IMAGE_LOSSY as _IMAGE_LOSSY,
)
from ._common import (
    _IMAGE_PIL_FORMATS as _IMAGE_PIL_FORMATS,
)
from ._common import (
    _OPTIONAL_BACKENDS as _OPTIONAL_BACKENDS,
)
from ._common import (
    _PROGRESS_CHUNK as _PROGRESS_CHUNK,
)
from ._common import (
    _PROGRESS_EST_AVG_BYTES_PER_LINE as _PROGRESS_EST_AVG_BYTES_PER_LINE,
)
from ._common import (
    _PROGRESS_MIN_INTERVAL_S as _PROGRESS_MIN_INTERVAL_S,
)
from ._common import (
    _SQL_IDENTIFIER_RE as _SQL_IDENTIFIER_RE,
)
from ._common import (
    IMAGE_EXTENSIONS as IMAGE_EXTENSIONS,
)
from ._common import (
    LOGGER as LOGGER,
)
from ._common import (
    READERS as READERS,
)
from ._common import (
    WRITERS as WRITERS,
)
from ._common import (
    CanonicalRecords as CanonicalRecords,
)
from ._common import (
    ConversionCancelledError as ConversionCancelledError,
)
from ._common import (
    ConversionIssue as ConversionIssue,
)
from ._common import (
    ConversionStatus as ConversionStatus,
)
from ._common import (
    ConvertResult as ConvertResult,
)
from ._common import (
    ReaderFn as ReaderFn,
)
from ._common import (
    WriterFn as WriterFn,
)
from ._common import (
    _ensure_parent_dir as _ensure_parent_dir,
)
from ._common import (
    _missing_backend_hint as _missing_backend_hint,
)
from ._common import (
    _ordered_columns as _ordered_columns,
)
from ._common import (
    _ProgressEmitter as _ProgressEmitter,
)
from ._common import (
    _record_rejection as _record_rejection,
)
from ._common import (
    _require_file as _require_file,
)
from ._common import (
    atomic_output as atomic_output,
)
from ._common import (
    check_cancel as check_cancel,
)
from ._common import (
    image_suffix as image_suffix,
)
from ._common import (
    is_image_pair as is_image_pair,
)
from ._common import (
    normalize_format_spec as normalize_format_spec,
)
from ._common import (
    normalize_image_extension as normalize_image_extension,
)
from ._common import (
    register_reader as register_reader,
)
from ._common import (
    register_writer as register_writer,
)
from ._common import (
    sniff_format as sniff_format,
)
from ._csv import (
    _detect_csv_encoding as _detect_csv_encoding,
)
from ._csv import (
    _file_decodes_strictly as _file_decodes_strictly,
)
from ._csv import (
    _iter_csv as _iter_csv,
)
from ._csv import (
    _write_csv_iter as _write_csv_iter,
)
from ._csv import (
    read_csv as read_csv,
)
from ._csv import (
    write_csv as write_csv,
)
from ._duckdb import (
    _BUILTIN_DUCKDB_READER as _BUILTIN_DUCKDB_READER,
)
from ._duckdb import (
    _BUILTIN_DUCKDB_WRITER as _BUILTIN_DUCKDB_WRITER,
)
from ._duckdb import (
    _VALID_COLUMN_RE as _VALID_COLUMN_RE,
)
from ._duckdb import (
    _duckdb_add_kind as _duckdb_add_kind,
)
from ._duckdb import (
    _duckdb_type_name as _duckdb_type_name,
)
from ._duckdb import (
    _duckdb_validate_columns as _duckdb_validate_columns,
)
from ._duckdb import (
    _iter_duckdb as _iter_duckdb,
)
from ._duckdb import (
    _plan_duckdb_schema as _plan_duckdb_schema,
)
from ._duckdb import (
    _register_duckdb as _register_duckdb,
)
from ._duckdb import (
    _write_duckdb_batches as _write_duckdb_batches,
)
from ._excel import (
    _BUILTIN_XLSX_READER as _BUILTIN_XLSX_READER,
)
from ._excel import (
    _BUILTIN_XLSX_WRITER as _BUILTIN_XLSX_WRITER,
)
from ._excel import (
    XLSX_ROW_LIMIT as XLSX_ROW_LIMIT,
)
from ._excel import (
    _iter_xlsx as _iter_xlsx,
)
from ._excel import (
    _register_xlsx as _register_xlsx,
)
from ._excel import (
    _write_xlsx_iter as _write_xlsx_iter,
)
from ._jsonl import (
    _flatten_to as _flatten_to,
)
from ._jsonl import (
    _iter_jsonl as _iter_jsonl,
)
from ._jsonl import (
    _iter_jsonl_with_digest as _iter_jsonl_with_digest,
)
from ._jsonl import (
    _write_jsonl_iter as _write_jsonl_iter,
)
from ._jsonl import (
    read_jsonl as read_jsonl,
)
from ._jsonl import (
    write_jsonl as write_jsonl,
)
from ._parquet import (
    _BUILTIN_PARQUET_READER as _BUILTIN_PARQUET_READER,
)
from ._parquet import (
    _BUILTIN_PARQUET_WRITER as _BUILTIN_PARQUET_WRITER,
)
from ._parquet import (
    _iter_parquet as _iter_parquet,
)
from ._parquet import (
    _parquet_chunk_table as _parquet_chunk_table,
)
from ._parquet import (
    _plan_parquet_schema as _plan_parquet_schema,
)
from ._parquet import (
    _register_parquet as _register_parquet,
)
from ._parquet import (
    _write_parquet_batches as _write_parquet_batches,
)
