"""B-2 值级数据变换（services/data_transform.py + commands/transform.py）测试。

覆盖：--map 解析、--transform-steps 值级翻译（含非法算子/缺参报错）、
transform_records（追加 _parsed 列、原列保留、求值失败容错）、
transform_file（CSV→JSONL 端到端、max_records、dry-run 不写、preview）、
CLI execute 安全门（默认不写，--confirm 才写）。
"""

from __future__ import annotations

import csv as _csv_module
import json
import tempfile
import unittest
from pathlib import Path

import pytest

from omnicrawl.commands.transform import execute as transform_cli
from omnicrawl.services.data_transform import (
    MapSpec,
    build_specs,
    parse_map,
    transform_file,
    transform_records,
    translate_steps,
)


def _csv(path: Path, rows: list[tuple[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = _csv_module.writer(handle)
        writer.writerow(["price", "title"])
        for a, b in rows:
            writer.writerow([a, b])
    return path


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── parse_map / build_specs ──────────────────────────────────


def test_parse_map_basic() -> None:
    spec = parse_map("price = parse_money(price)")
    assert spec.column == "price"
    assert spec.expression == "parse_money(price)"
    assert spec.output_column == "price_parsed"


def test_parse_map_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="格式"):
        parse_map("no-equals")
    with pytest.raises(ValueError, match="目标列名"):
        parse_map("  = trim(x)")
    with pytest.raises(ValueError, match="表达式"):
        parse_map("x =   ")


def test_build_specs_requires_at_least_one_mapping() -> None:
    with pytest.raises(ValueError, match="至少一个"):
        build_specs()


def test_build_specs_merges_maps_and_steps() -> None:
    steps = json.dumps([
        {"type": "trim", "field": "title"},
        {"type": "parse_money", "field": "price", "options": {"unit": "元"}},
        {"type": "regex_extract", "field": "code", "options": {"pattern": r"#(\d+)"}},
        {"type": "coalesce", "field": "a", "options": {"fields": ["b", "c"]}},
    ])
    specs = build_specs(["tag = trim(tag)"], transform_steps=steps)
    assert [s.column for s in specs] == ["tag", "title", "price", "code", "a"]
    assert specs[1].expression == 'trim("title")'
    assert specs[2].expression == 'parse_money("price", "元")'
    assert "regex_extract" in specs[3].expression and '"code"' in specs[3].expression
    assert specs[4].expression == 'coalesce("a", "b", "c")'


def test_translate_steps_rejects_non_value_ops() -> None:
    with pytest.raises(ValueError, match="不是值级算子"):
        translate_steps([{"type": "dedupe", "field": "url"}])


def test_translate_steps_requires_field_and_pattern() -> None:
    with pytest.raises(ValueError, match="缺少 field"):
        translate_steps([{"type": "trim"}])
    with pytest.raises(ValueError, match="pattern"):
        translate_steps([{"type": "regex_extract", "field": "x"}])


# ── transform_records ────────────────────────────────────────


def test_transform_records_appends_parsed_columns() -> None:
    records = [{"price": "¥1,299 元", "title": "  A  "}]
    specs = [MapSpec("price", "parse_money(price)"), MapSpec("title", "trim(title)")]
    new, stats = transform_records(records, specs)
    assert new[0]["price"] == "¥1,299 元"  # 原列永不改写
    assert new[0]["price_parsed"] == "1299"
    assert new[0]["title_parsed"] == "A"
    assert stats.columns_added == ("price_parsed", "title_parsed")
    assert stats.rows == 1
    assert stats.eval_failures == 0


def test_transform_records_keeps_original_column_untouched() -> None:
    records = [{"price": "¥20"}]
    specs = [MapSpec("price", "concat(price, '_x')")]
    new, _ = transform_records(records, specs)
    assert new[0]["price"] == "¥20"
    assert new[0]["price_parsed"] == "¥20_x"


def test_transform_records_failure_writes_none_and_counts() -> None:
    records = [{"x": "1"}]
    specs = [MapSpec("x", "missing_fn(x)")]  # 未允许函数 → safe_eval 拒绝
    new, stats = transform_records(records, specs)
    assert new[0]["x_parsed"] is None
    assert stats.eval_failures == 1
    assert stats.failure_samples


# ── transform_file ───────────────────────────────────────────


def test_transform_file_csv_to_jsonl(tmp_path: Path) -> None:
    src = _csv(tmp_path / "in.csv", [("¥1,299 元", "A"), ("¥20", "B")])
    dst = tmp_path / "out.jsonl"
    result = transform_file(src, dst, [MapSpec("price", "parse_money(price)")])
    assert result["written"] is True
    assert result["rows"] == 2
    rows = _read_jsonl(dst)
    assert rows[0]["price"] == "¥1,299 元"
    assert rows[0]["price_parsed"] == "1299"
    assert rows[1]["price_parsed"] == "20"


def test_transform_file_dry_run_writes_nothing(tmp_path: Path) -> None:
    src = _csv(tmp_path / "in.csv", [("¥1", "A")])
    result = transform_file(src, None, [MapSpec("price", "parse_money(price)")])
    assert result["written"] is False
    assert result["output"] is None
    assert result["rows"] == 1


def test_transform_file_max_records(tmp_path: Path) -> None:
    src = _csv(tmp_path / "in.csv", [("¥1", "A"), ("¥2", "B"), ("¥3", "C")])
    dst = tmp_path / "out.jsonl"
    result = transform_file(src, dst, [MapSpec("price", "parse_money(price)")], max_records=2)
    assert result["rows"] == 2
    assert len(_read_jsonl(dst)) == 2


def test_transform_file_preview_limited_columns(tmp_path: Path) -> None:
    src = _csv(tmp_path / "in.csv", [("¥1", "A")])
    result = transform_file(
        src, None, [MapSpec("price", "parse_money(price)")], preview_limit=1,
    )
    assert len(result["preview"]) == 1
    assert set(result["preview"][0]) == {"price", "price_parsed"}


def test_transform_file_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        transform_file(tmp_path / "nope.csv", None, [MapSpec("x", "trim(x)")])


# ── commands.transform 安全门 ────────────────────────────────


class TransformCliTest(unittest.TestCase):
    def test_default_is_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            src = _csv(temp / "in.csv", [("¥1", "A")])
            result = transform_cli(str(src), str(temp / "out.jsonl"), maps=["price = parse_money(price)"])
            self.assertEqual(result["mode"], "dry-run")
            self.assertFalse((temp / "out.jsonl").exists())
            self.assertIn("note", result)

    def test_confirm_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            src = _csv(temp / "in.csv", [("¥1", "A")])
            dst = temp / "out.jsonl"
            result = transform_cli(str(src), str(dst), maps=["price = parse_money(price)"], confirm=True)
            self.assertEqual(result["mode"], "write")
            self.assertTrue(dst.exists())
            self.assertEqual(result["written"], True)

    def test_dry_run_beats_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            src = _csv(temp / "in.csv", [("¥1", "A")])
            dst = temp / "out.jsonl"
            result = transform_cli(str(src), str(dst), maps=["x = trim(x)"], dry_run=True, confirm=True)
            self.assertEqual(result["mode"], "dry-run")
            self.assertFalse(dst.exists())

    def test_confirm_requires_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            src = _csv(Path(temp) / "in.csv", [("¥1", "A")])
            with self.assertRaises(ValueError, msg="目标文件"):
                transform_cli(str(src), None, maps=["x = trim(x)"], confirm=True)

    def test_no_maps_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            src = _csv(Path(temp) / "in.csv", [("¥1", "A")])
            with self.assertRaises(ValueError):
                transform_cli(str(src), None)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
