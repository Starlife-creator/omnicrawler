"""Security and correctness tests for the DuckDB export path.

Covers the column-name whitelist validation (ADR-0001) and basic
DuckDB export functionality including normal columns, None values,
and large column counts.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from omnicrawl.core.config import load_config
from omnicrawl.pipeline.exporters import _validate_column_names, export_all
from omnicrawl.state import StateStore

# ── Column name validation (ADR-0001) ──────────────────────────

class TestColumnValidation:
    """Tests for _validate_column_names whitelist enforcement."""

    def test_valid_simple_names(self) -> None:
        """Standard alphanumeric+underscore names should pass."""
        result = _validate_column_names(["record_id", "title", "url", "_private", "col2"])
        assert result == ["record_id", "title", "url", "_private", "col2"]

    def test_valid_single_underscore(self) -> None:
        """A lone underscore is a valid SQL identifier start."""
        assert _validate_column_names(["_"]) == ["_"]

    def test_valid_mixed_case(self) -> None:
        """Mixed case names are allowed by the whitelist."""
        assert _validate_column_names(["CamelCase", "snake_case", "SCREAMING"]) is not None

    def test_reject_empty_string(self) -> None:
        """Empty column names must be rejected."""
        with pytest.raises(ValueError, match="Invalid DuckDB column name"):
            _validate_column_names(["valid", ""])

    def test_reject_space_in_name(self) -> None:
        """Spaces in column names must be rejected."""
        with pytest.raises(ValueError, match="Invalid DuckDB column name"):
            _validate_column_names(["hello world"])

    def test_reject_special_chars(self) -> None:
        """SQL-special characters must be rejected."""
        for bad_name in ['col"; DROP TABLE', "col' OR 1=1", "col--comment", "col/*x*/", "col\ninjection"]:
            with pytest.raises(ValueError, match="Invalid DuckDB column name"):
                _validate_column_names([bad_name])

    def test_reject_digit_start(self) -> None:
        """Names starting with a digit are invalid SQL identifiers."""
        with pytest.raises(ValueError, match="Invalid DuckDB column name"):
            _validate_column_names(["123abc"])

    def test_reject_chinese_name(self) -> None:
        """Non-ASCII (e.g. Chinese) column names must be rejected."""
        with pytest.raises(ValueError, match="Invalid DuckDB column name"):
            _validate_column_names(["标题"])

    def test_reject_hyphen(self) -> None:
        """Hyphens are not valid in SQL identifiers."""
        with pytest.raises(ValueError, match="Invalid DuckDB column name"):
            _validate_column_names(["my-column"])

    def test_reject_dot_notation(self) -> None:
        """Dotted names (from nested data flattening) must be rejected."""
        with pytest.raises(ValueError, match="Invalid DuckDB column name"):
            _validate_column_names(["user.name"])

    def test_reject_none_in_list(self) -> None:
        """None values in column list must be rejected (match will fail)."""
        with pytest.raises((ValueError, TypeError)):
            _validate_column_names(["valid", None])  # type: ignore[list-item]

    def test_empty_list_passes(self) -> None:
        """An empty column list should pass validation (no columns to check)."""
        assert _validate_column_names([]) == []

    def test_reject_among_valid(self) -> None:
        """One invalid name among valid ones must be caught."""
        with pytest.raises(ValueError, match="Invalid DuckDB column name"):
            _validate_column_names(["record_id", "title", "bad name", "url"])

    def test_many_valid_columns(self) -> None:
        """Large number of valid columns should not cause issues."""
        columns = [f"column_{i}" for i in range(500)]
        result = _validate_column_names(columns)
        assert len(result) == 500


# ── DuckDB export integration ──────────────────────────────────

def _make_config(tmp_path: Path, outputs: dict[str, bool]) -> tuple[Path, load_config]:
    """Create a minimal config with specified output formats."""
    config_path = tmp_path / "project.yaml"
    config_path.write_text(yaml.safe_dump({
        "project": {"name": "duckdb_test", "workspace": str(tmp_path / "work")},
        "source": {"kind": "static_html", "seeds": ["https://example.org"]},
        "http": {"user_agent": "DuckDBTest/1.0 (+contact: test@example.org)"},
        "outputs": outputs,
    }), encoding="utf-8")
    return config_path, load_config(config_path)


def _insert_record(state: StateStore, run_id: str, data: dict) -> None:
    """Insert a test record into the state store."""
    state.conn.execute(
        "INSERT INTO records (record_id, run_id, request_fingerprint, source_url, record_type, data_json, evidence_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        ("rec_001", run_id, "fp001", "https://example.org", "page",
         json.dumps(data, ensure_ascii=False), json.dumps({})),
    )
    state.conn.commit()


class TestDuckDBExport:
    """Integration tests for DuckDB export with column validation."""

    def test_duckdb_export_normal_columns(self, tmp_path: Path) -> None:
        """Normal column names should export successfully to DuckDB."""
        try:
            import duckdb  # noqa: F401
        except ImportError:
            pytest.skip("duckdb not installed")

        config_path, config = _make_config(tmp_path, {"jsonl": False, "csv": False, "duckdb": True})
        with StateStore(config.workspace / "state.sqlite3") as state:
            run_id = state.start_run("duckdb_test", str(config_path))
            _insert_record(state, run_id, {"title": "Test", "url": "https://example.org", "count": 42})
            summary = export_all(config, state, run_id)

        assert "duckdb" in summary["files"]
        duckdb_path = Path(summary["files"]["duckdb"])
        assert duckdb_path.exists()

        import duckdb
        conn = duckdb.connect(str(duckdb_path))
        try:
            result = conn.execute("SELECT COUNT(*) FROM records").fetchone()
            assert result[0] == 1
            cols = [desc[0] for desc in conn.execute("SELECT * FROM records LIMIT 0").description]
            assert "title" in cols
            assert "url" in cols
        finally:
            conn.close()

    def test_duckdb_export_rejects_invalid_column(self, tmp_path: Path) -> None:
        """Column names with special characters should raise ValueError."""
        try:
            import duckdb  # noqa: F401
        except ImportError:
            pytest.skip("duckdb not installed")

        config_path, config = _make_config(tmp_path, {"jsonl": False, "csv": False, "duckdb": True})
        with StateStore(config.workspace / "state.sqlite3") as state:
            run_id = state.start_run("duckdb_test", str(config_path))
            # Insert a record with an invalid column name (contains a dot from flattening)
            _insert_record(state, run_id, {"valid_col": "ok", "bad.name": "evil"})
            with pytest.raises(ValueError, match="Invalid DuckDB column name"):
                export_all(config, state, run_id)

    def test_duckdb_export_none_values(self, tmp_path: Path) -> None:
        """None values in data should be handled gracefully by DuckDB export."""
        try:
            import duckdb  # noqa: F401
        except ImportError:
            pytest.skip("duckdb not installed")

        config_path, config = _make_config(tmp_path, {"jsonl": False, "csv": False, "duckdb": True})
        with StateStore(config.workspace / "state.sqlite3") as state:
            run_id = state.start_run("duckdb_test", str(config_path))
            _insert_record(state, run_id, {"title": "Test", "nullable_field": None})
            summary = export_all(config, state, run_id)

        assert "duckdb" in summary["files"]

    def test_duckdb_export_empty_records(self, tmp_path: Path) -> None:
        """DuckDB export with no records should still create a valid database."""
        try:
            import duckdb  # noqa: F401
        except ImportError:
            pytest.skip("duckdb not installed")

        config_path, config = _make_config(tmp_path, {"jsonl": False, "csv": False, "duckdb": True})
        with StateStore(config.workspace / "state.sqlite3") as state:
            run_id = state.start_run("duckdb_test", str(config_path))
            summary = export_all(config, state, run_id)

        assert "duckdb" in summary["files"]
        duckdb_path = Path(summary["files"]["duckdb"])
        assert duckdb_path.exists()

    def test_duckdb_graceful_without_library(self, tmp_path: Path) -> None:
        """When duckdb is not installed, export should add a warning."""
        config_path, config = _make_config(tmp_path, {"jsonl": False, "csv": False, "duckdb": True})
        with StateStore(config.workspace / "state.sqlite3") as state:
            run_id = state.start_run("duckdb_test", str(config_path))
            with patch.dict("sys.modules", {"duckdb": None}):
                summary = export_all(config, state, run_id)

        # DuckDB file should not be in outputs, but a warning should explain why
        assert "duckdb" not in summary["files"]
        assert any("duckdb" in w.lower() for w in summary["warnings"])
