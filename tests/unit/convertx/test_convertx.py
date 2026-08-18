"""P3-2：ConvertX 任意格式互转 —— 5 Reader × 5 Writer = N×N 矩阵 + CLI 子命令。

注意：XLSX / Parquet / DuckDB 依赖缺失时，相关用例用 pytest.importorskip 跳过，不阻塞核心 4×4（CSV/JSONL）矩阵的绿色门禁。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from omnicrawler.convertx import (
    READERS,
    WRITERS,
    CanonicalRecords,
    ConvertResult,
    _ordered_columns,
    convert,
    read_csv,
    read_jsonl,
    sniff_format,
    write_csv,
    write_jsonl,
)

# ── Fixtures ─────────────────────────────────────────────
SAMPLE: CanonicalRecords = [
    {
        "record_id": "r1",
        "source_url": "https://example.com/p1",
        "record_type": "product",
        "created_at": "2026-08-01T10:00:00Z",
        "title": "笔记本电脑 Pro",
        "price": 6999,
        "in_stock": True,
    },
    {
        "record_id": "r2",
        "source_url": "https://example.com/p2",
        "record_type": "product",
        "created_at": "2026-08-01T11:00:00Z",
        "title": "无线鼠标",
        "price": 99.5,
        "in_stock": False,
    },
]

NESTED_JSONL_SAMPLE = [
    {
        "record_id": "n1",
        "source_url": "https://example.com/x",
        "record_type": "article",
        "created_at": "2026-08-02T00:00:00Z",
        "data": {"headline": "大模型进化", "tags": ["ai", "llm"], "author": {"name": "L", "org": "R"}},
        "evidence": {"selector": "h1", "confidence": 0.97},
    }
]


# ── Basic sniffing + columns ───────────────────────────
class TestBasics:
    def test_sniff_format(self, tmp_path: Path) -> None:
        assert sniff_format(Path("records.jsonl")) == ".jsonl"
        assert sniff_format(Path("records.CSV")) == ".csv"
        assert sniff_format(Path("records.ndjson")) == ".jsonl"
        assert sniff_format(Path("records.xyz")) is None
        # .db → .duckdb 别名
        assert sniff_format(Path("records.db")) == ".duckdb"

    def test_registry_defaults_exist(self) -> None:
        # 4 核心 Reader + Parquet/XLSX/DuckDB 按依赖可选，CSV/JSONL 必存在
        assert ".csv" in READERS
        assert ".jsonl" in READERS
        assert ".csv" in WRITERS
        assert ".jsonl" in WRITERS

    def test_ordered_columns_base_first(self) -> None:
        cols = _ordered_columns([{"foo": 1, "source_url": "x"}])
        # base 必须在前（BASE_COLUMNS 顺序）
        base_idx = [cols.index(c) for c in ("record_id", "source_url", "record_type", "created_at")]
        assert base_idx[0] < base_idx[1] < base_idx[2] < base_idx[3]
        assert "foo" in cols

    def test_read_csv_encoding_auto_gbk(self, tmp_path: Path) -> None:
        """S3：encoding='auto' 自动检测 GBK 编码（chardet 缺失时跳过）。"""
        pytest.importorskip("chardet")
        src = tmp_path / "gbk.csv"
        text = "名称,数值\n" + ("笔记本电脑,6999\n" * 10)
        src.write_bytes(text.encode("gbk"))
        rows = read_csv(src, {"encoding": "auto"})
        assert rows
        assert rows[0]["名称"] == "笔记本电脑"
        assert rows[0]["数值"] == "6999"

    def test_read_csv_encoding_default_utf8_unchanged(self, tmp_path: Path) -> None:
        """S3：默认 encoding 仍为 utf-8-sig，现有行为零变化。"""
        src = tmp_path / "utf8.csv"
        src.write_text("名称,数值\n商品A,1\n", encoding="utf-8-sig")
        rows = read_csv(src, {})
        assert rows[0]["名称"] == "商品A"


# ── CSV ↔ JSONL ↔ 回读 = 往返一致性 ───────────────────────
class TestCoreMatrix:
    def test_csv_roundtrip(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "out.csv"
        write_csv(SAMPLE, csv_file, {})
        back = read_csv(csv_file, {})
        assert back[0]["record_id"] == "r1"
        # CSV 会把 6999 / 99.5 变成字符串 → 回读字符串没问题
        assert back[0]["title"] == "笔记本电脑 Pro"
        assert len(back) == 2

    def test_jsonl_flat_roundtrip(self, tmp_path: Path) -> None:
        jf = tmp_path / "out.jsonl"
        write_jsonl(SAMPLE, jf, {})
        back = read_jsonl(jf, {})
        # 顺序一致
        assert [r["record_id"] for r in back] == ["r1", "r2"]
        assert back[0]["price"] == 6999  # JSON 保留原生类型

    def test_jsonl_nested_flattens(self, tmp_path: Path) -> None:
        jf = tmp_path / "nested.jsonl"
        with jf.open("w", encoding="utf-8") as fh:
            for row in NESTED_JSONL_SAMPLE:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        back = read_jsonl(jf, {"flat": True})
        assert len(back) == 1
        rec = back[0]
        # evidence_json → 以 JSON 字符串存在
        assert isinstance(rec["evidence_json"], str)
        ev = json.loads(rec["evidence_json"])
        assert ev["confidence"] == 0.97
        # 嵌套 dict：author.name / author.org 直接 JSON 序列化
        assert "author" in rec  # flatten_to 按 key 直接写（data 下 author 的 val 是 dict → dump 成 str 存）
        # headline / tags 都要存在
        assert rec["headline"] == "大模型进化"
        # tags 是 list → dump 成字符串
        assert "ai" in str(rec["tags"])

    def test_jsonl_nested_writer_then_readback(self, tmp_path: Path) -> None:
        """JSONL Writer nested=True 输出 pipeline 原生格式，再 Reader flat 能解析回来。"""
        out = tmp_path / "pipeline.jsonl"
        write_jsonl(SAMPLE, out, {"nested": True})
        # 每一行都必须有 data 键
        raw = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert "data" in raw[0]
        assert raw[0]["data"]["title"] == "笔记本电脑 Pro"
        # flat reader 回读
        back = read_jsonl(out, {"flat": True})
        assert len(back) == 2
        assert back[0]["title"] == "笔记本电脑 Pro"
        assert back[0]["price"] == 6999

    def test_convert_csv_to_jsonl(self, tmp_path: Path) -> None:
        csv_p = tmp_path / "in.csv"
        jsonl_p = tmp_path / "out.jsonl"
        write_csv(SAMPLE, csv_p, {})
        result = convert(csv_p, jsonl_p)
        assert isinstance(result, ConvertResult)
        assert result.source_format == ".csv"
        assert result.target_format == ".jsonl"
        assert result.rows == 2
        assert result.output_path == jsonl_p.resolve() or result.output_path == jsonl_p
        # JSONL 内容存在
        assert jsonl_p.stat().st_size > 0

    def test_convert_jsonl_to_csv(self, tmp_path: Path) -> None:
        jf = tmp_path / "in.jsonl"
        cf = tmp_path / "out.csv"
        write_jsonl(SAMPLE, jf, {})
        result = convert(jf, cf)
        assert result.rows == 2
        assert cf.suffix == ".csv"
        assert cf.stat().st_size > 0

    def test_convert_same_path_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "same.jsonl"
        write_jsonl(SAMPLE, p, {})
        with pytest.raises(ValueError, match="源与目标路径相同"):
            convert(p, p)

    def test_convert_same_file_rel_and_abs_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """相对路径与绝对路径指向同一文件也必须拒绝覆盖（resolve 后比较）。"""
        p = tmp_path / "same_rel.jsonl"
        write_jsonl(SAMPLE, p, {})
        monkeypatch.chdir(tmp_path)  # 使相对路径与绝对路径处于同一盘符
        rel = Path(os.path.relpath(p))
        with pytest.raises(ValueError, match="源与目标路径相同"):
            convert(rel, p)
        with pytest.raises(ValueError, match="源与目标路径相同"):
            convert(p, rel)


    def test_convert_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            convert(tmp_path / "nope.csv", tmp_path / "x.jsonl")

    def test_convert_unknown_format_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "x.not_exist"
        src.write_text("dummy\n")
        with pytest.raises(KeyError, match="不支持的源格式"):
            convert(src, tmp_path / "y.jsonl")


# ── JSONL 容错 + 行进度回调 ──────────────────────────────
class TestJsonlErrorResilience:
    def test_malformed_lines_skipped_by_default(self, tmp_path: Path) -> None:
        """on_error='skip'（默认）：损坏行被跳过，其余正常解析。"""
        jf = tmp_path / "bad.jsonl"
        lines = [
            '{"record_id": "ok1", "title": "a"}',
            '{this is not json at all!!!',   # malformed
            '   ',                            # blank, skipped
            '{"record_id": "ok2", "title": "b"}',
            '{"truncated": true',             # malformed
        ]
        jf.write_text("\n".join(lines) + "\n", encoding="utf-8")
        back = read_jsonl(jf, {})
        # 只保留了 2 条合法记录
        ids = [r.get("record_id") for r in back]
        assert ids == ["ok1", "ok2"]

    def test_malformed_lines_abort_mode_raises(self, tmp_path: Path) -> None:
        """on_error='abort'：遇到任一损坏行直接抛 ValueError。"""
        jf = tmp_path / "bad.jsonl"
        jf.write_text(
            '{"record_id": "ok1"}\n{"not valid json\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="JSONL 解析失败"):
            read_jsonl(jf, {"on_error": "abort"})

    def test_on_line_progress_callback_fires(self, tmp_path: Path) -> None:
        """on_line_progress 回调被按行调用（包含已解析记录数累积）。"""
        jf = tmp_path / "prog.jsonl"
        write_jsonl(SAMPLE, jf, {})
        events: list[dict] = []
        read_jsonl(jf, {"on_line_progress": lambda ev: events.append(dict(ev))})
        # SAMPLE 有 2 行，每成功一行触发一次
        assert len(events) >= 2
        # 最后一次 events 中的 records_so_far 应 == 2
        assert events[-1].get("records_so_far") == 2

    def test_convert_named_params_flat_nested(self, tmp_path: Path) -> None:
        """convert() 具名参数 flat/nested/table/compression 能正确透传（无需写 options dict）。"""
        jf = tmp_path / "in.jsonl"
        cf = tmp_path / "out.csv"
        write_jsonl(SAMPLE, jf, {})
        # 使用具名参数（不是 options dict）
        result = convert(
            jf, cf,
            flat=True,
            nested=False,
            table="records",
            compression="zstd",
            on_error="skip",
        )
        assert isinstance(result, ConvertResult)
        assert result.rows == 2
        assert cf.stat().st_size > 0


# ── 可选依赖测试：XLSX / Parquet / DuckDB ───────────────
class TestOptionalFormats:
    def test_xlsx_roundtrip(self, tmp_path: Path) -> None:
        pytest.importorskip("openpyxl")
        xlsx = tmp_path / "o.xlsx"
        from omnicrawler.convertx import WRITERS as W

        assert ".xlsx" in W
        W[".xlsx"](SAMPLE, xlsx, {})
        back = READERS[".xlsx"](xlsx, {})
        assert len(back) == 2
        # XLSX 支持原生 int/float/bool
        assert back[0]["price"] == 6999
        assert back[0]["in_stock"] is True
        assert back[1]["price"] == 99.5
        # 标题栏被读取为表头，source_url 仍然是字符串
        assert back[0]["source_url"] == "https://example.com/p1"

    def test_parquet_roundtrip(self, tmp_path: Path) -> None:
        pytest.importorskip("pyarrow")
        from omnicrawler.convertx import WRITERS as W

        pq = tmp_path / "o.parquet"
        assert ".parquet" in W
        W[".parquet"](SAMPLE, pq, {})
        back = READERS[".parquet"](pq, {})
        assert len(back) == 2
        # zstd 通常压缩比 > 4x（对于这种小样本可能不一样，但至少有内容）
        assert 0 < pq.stat().st_size

    def test_duckdb_roundtrip(self, tmp_path: Path) -> None:
        pytest.importorskip("duckdb")
        from omnicrawler.convertx import WRITERS as W

        db = tmp_path / "data.duckdb"
        assert ".duckdb" in W
        W[".duckdb"](SAMPLE, db, {"table": "records"})
        assert db.exists() and db.stat().st_size > 0
        back = READERS[".duckdb"](db, {"table": "records"})
        assert len(back) == 2
        # DuckDB 类型推断：price DOUBLE 或 BIGINT 都可接受
        ids = sorted([b["record_id"] for b in back])
        assert ids == ["r1", "r2"]

    def test_duckdb_table_whitelist_rejects_injection(self, tmp_path: Path) -> None:
        """META：table 标识符白名单——SQL 注入形态表名必须被拒。"""
        pytest.importorskip("duckdb")
        import duckdb

        from omnicrawler.convertx import READERS

        db = tmp_path / "data.duckdb"
        con = duckdb.connect(str(db))
        con.execute("CREATE TABLE records AS SELECT 1 AS record_id")
        con.close()
        for bad in ("records; DROP TABLE x", "records--", "main.records.extra", "1;2"):
            with pytest.raises(ValueError, match="无效的 duckdb 表名"):
                READERS[".duckdb"](db, {"table": bad})


# ── CLI 子命令：端到端 ─────────────────────────────────
class TestCLI:
    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        import os
        env = os.environ.copy()
        # 把 src 目录注入 PYTHONPATH，确保子进程能找到 omnicrawler 包
        # __file__ = <repo>/tests/unit/convertx/test_convertx.py → parents[3] 是仓库根
        repo_root = Path(__file__).resolve().parents[3]
        src_root = repo_root / "src"
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(src_root) + (os.pathsep + existing_pp if existing_pp else "")
        return subprocess.run(
            [sys.executable, "-m", "omnicrawler.cli", *args],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=repo_root,
            env=env,
        )

    def test_convert_csv_to_jsonl_end_to_end(self, tmp_path: Path) -> None:
        # 直接用 convertx 写一份 CSV 源
        src = tmp_path / "r.csv"
        dst = tmp_path / "r.jsonl"
        write_csv(SAMPLE, src, {})
        proc = self._cli(
            "convert",
            "--from", str(src),
            "--to", str(dst),
            "--quiet",
        )
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True
        assert payload["rows"] == 2
        assert payload["source_format"] == ".csv"
        assert payload["target_format"] == ".jsonl"
        assert dst.exists() and dst.stat().st_size > 0

    def test_convert_jsonl_nested_flag_end_to_end(self, tmp_path: Path) -> None:
        """--nested：Writer JSONL 输出 pipeline 原始 records.jsonl 结构。"""
        src = tmp_path / "r.csv"
        dst = tmp_path / "records.jsonl"
        write_csv(SAMPLE, src, {})
        proc = self._cli(
            "convert",
            "-f", str(src),
            "-t", str(dst),
            "--nested",
            "--quiet",
        )
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        lines = [json.loads(line) for line in dst.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert "data" in lines[0]
        assert "evidence" in lines[0]
