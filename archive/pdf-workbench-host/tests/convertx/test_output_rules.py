"""ConvertX 输出策略与状态模型测试。

覆盖：命名模板、非法字符清洗、四种冲突策略、源文件保护、时间戳保留，
以及 ConvertResult.status / issues 对“部分成功”的显式表达。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from omnicrawler.convertx import (
    ConversionStatus,
    OutputRule,
    convert,
    resolve_output_path,
    sanitize_filename,
)
from omnicrawler.convertx.output_rules import apply_timestamps


class TestSanitize:
    def test_illegal_characters_and_trailing_dots(self) -> None:
        assert sanitize_filename('a<>:"/\\|?*b. ') == "a_________b"
        assert sanitize_filename("   ") == "output"
        assert sanitize_filename("con") == "_con"

    def test_length_is_bounded(self) -> None:
        assert len(sanitize_filename("x" * 300)) == 200


class TestTemplate:
    def test_tokens_and_auto_extension(self, tmp_path: Path) -> None:
        rule = OutputRule(template="{name}_{date}_{time}_{index}")
        resolution = resolve_output_path(
            tmp_path / "in.csv", tmp_path / "out.csv", rule,
        )
        assert resolution.path is not None
        assert resolution.path.suffix == ".csv"
        assert resolution.path.stem.startswith("out_")
        assert resolution.path.stem.endswith("_1")

    def test_unknown_token_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知占位符"):
            OutputRule(template="{name}_{bogus}")

    def test_directory_override(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "nested"
        rule = OutputRule(directory=target_dir)
        resolution = resolve_output_path(tmp_path / "in.csv", tmp_path / "out.csv", rule)
        assert resolution.path == target_dir / "out.csv"


class TestConflictPolicies:
    def test_overwrite_existing(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        dst = tmp_path / "out.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        dst.write_text("old", encoding="utf-8")
        resolution = resolve_output_path(src, dst, OutputRule(on_conflict="overwrite"))
        assert resolution.path == dst

    def test_skip_existing(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        dst = tmp_path / "out.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        dst.write_text("old", encoding="utf-8")
        resolution = resolve_output_path(src, dst, OutputRule(on_conflict="skip"))
        assert resolution.skipped is True
        assert resolution.path is None
        assert dst.read_text(encoding="utf-8") == "old"

    def test_error_existing(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        dst = tmp_path / "out.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        dst.write_text("old", encoding="utf-8")
        with pytest.raises(FileExistsError):
            resolve_output_path(src, dst, OutputRule(on_conflict="error"))

    def test_rename_existing_twice(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        dst = tmp_path / "out.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        dst.write_text("old", encoding="utf-8")
        (tmp_path / "out (2).csv").write_text("old2", encoding="utf-8")
        resolution = resolve_output_path(src, dst, OutputRule(on_conflict="rename"))
        assert resolution.path == tmp_path / "out (3).csv"

    def test_rename_uses_index_token(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        dst = tmp_path / "out.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        (tmp_path / "out_1.csv").write_text("old", encoding="utf-8")
        rule = OutputRule(template="{name}_{index}{ext}", on_conflict="rename")
        resolution = resolve_output_path(src, dst, rule)
        assert resolution.path == tmp_path / "out_2.csv"


class TestSourceProtection:
    def test_overwrite_source_is_refused(self, tmp_path: Path) -> None:
        src = tmp_path / "data.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="拒绝覆盖"):
            resolve_output_path(src, src, OutputRule(on_conflict="overwrite"))

    def test_rename_source_moves_aside(self, tmp_path: Path) -> None:
        src = tmp_path / "data.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        resolution = resolve_output_path(src, src, OutputRule(on_conflict="rename"))
        assert resolution.path == tmp_path / "data (2).csv"

    def test_skip_source(self, tmp_path: Path) -> None:
        src = tmp_path / "data.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        resolution = resolve_output_path(src, src, OutputRule(on_conflict="skip"))
        assert resolution.skipped is True
        assert resolution.path is None
        assert "相同" in resolution.reason


class TestConvertIntegration:
    def test_rename_keeps_both_outputs(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8-sig")
        dst = tmp_path / "out.jsonl"
        dst.write_text('{"keep": true}\n', encoding="utf-8")

        result = convert(src, dst, output_rule=OutputRule(on_conflict="rename"))

        assert result.status == ConversionStatus.COMPLETED
        assert dst.read_text(encoding="utf-8") == '{"keep": true}\n'
        assert result.output_path == tmp_path / "out (2).jsonl"
        assert result.output_path is not None and result.output_path.is_file()

    def test_skip_returns_explicit_status(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8-sig")
        dst = tmp_path / "out.jsonl"
        dst.write_text("old", encoding="utf-8")

        result = convert(src, dst, output_rule=OutputRule(on_conflict="skip"))

        assert result.status == ConversionStatus.SKIPPED
        assert result.output_path is None
        assert result.issues[0].code == "output_skipped"
        assert dst.read_text(encoding="utf-8") == "old"

    def test_preserve_timestamps(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8-sig")
        fixed_ns = 1_600_000_000_000_000_000
        os.utime(src, ns=(fixed_ns, fixed_ns))
        dst = tmp_path / "out.jsonl"

        result = convert(
            src, dst, output_rule=OutputRule(preserve_timestamps=True),
        )

        assert result.output_path == dst
        assert abs(dst.stat().st_mtime_ns - fixed_ns) < 2_000_000_000

    def test_apply_timestamps_reports_failure(self, tmp_path: Path) -> None:
        assert apply_timestamps(tmp_path / "missing", tmp_path / "out") is False

    def test_rejected_records_are_partial_success(self, tmp_path: Path) -> None:
        src = tmp_path / "bad.jsonl"
        src.write_text('{"a": 1}\nnot-json\n{"a": 2}\n', encoding="utf-8")
        dst = tmp_path / "out.csv"

        result = convert(src, dst, on_error="skip")

        assert result.status == ConversionStatus.COMPLETED_WITH_ERRORS
        assert result.extra["rejected_records"] == 1
        assert any(issue.code == "records_rejected" for issue in result.issues)

    def test_clean_conversion_status(self, tmp_path: Path) -> None:
        src = tmp_path / "in.jsonl"
        src.write_text('{"a": 1}\n', encoding="utf-8")
        result = convert(src, tmp_path / "out.csv")
        assert result.status == ConversionStatus.COMPLETED
        assert result.issues == []

    def test_explicit_format_without_leading_dot(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8-sig")
        dst = tmp_path / "out.data"  # 后缀无法识别，必须依赖显式格式
        result = convert(src, dst, src_format="csv", dst_format="jsonl")
        assert result.target_format == ".jsonl"
        assert dst.is_file()

    def test_legacy_default_still_overwrites(self, tmp_path: Path) -> None:
        src = tmp_path / "in.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8-sig")
        dst = tmp_path / "out.jsonl"
        dst.write_text("old", encoding="utf-8")

        result = convert(src, dst)

        assert result.status == ConversionStatus.COMPLETED
        assert json.loads(dst.read_text(encoding="utf-8").strip()) == {"a": "1", "b": "2"}
