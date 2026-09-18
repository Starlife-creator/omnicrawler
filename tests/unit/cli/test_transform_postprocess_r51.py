"""``transform`` 记录级后处理的**命令层契约**（走查 R5.1）。

这里只断言**用户看得见的东西**：JSON 回执字段、终端提示、以及"只有记录级操作也要能跑"
这类命令层判据 —— 那条判据原先只存在于 `build_specs` 里（它只有"映射"这一个概念），
于是 `--group-by 分类 --agg count` 会被误判成"没有任何操作"。

反向断言脚本：``.audit-tmp/w71/reverse_assert_r51.py``。
"""

from __future__ import annotations

import csv as _csv_module
from pathlib import Path

import pytest

from omnicrawler.commands.transform import execute

BOOKS: list[tuple[str, str, str]] = [
    ("小说", "£51.77", "A Light in the Attic"),
    ("小说", "£53.74", "Tipping the Velvet"),
    ("小说", "£50.10", "Soumission"),
    ("科普", "£12.99", "Sharp Objects"),
    ("科普", "£20.00", "Sapiens"),
    ("诗歌", "£8.50", "The Black Maria"),
]


def _books_csv(path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = _csv_module.writer(handle)
        writer.writerow(["分类", "价格", "标题"])
        writer.writerows(BOOKS)
    return path


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(_csv_module.DictReader(handle))


# ── 命令层判据：只有记录级操作也要能跑 ──────────────────────────────────


def test_sort_only_without_map_runs(tmp_path: Path) -> None:
    """★ 判据必须落在命令层：只有 `--sort` 而没有 `--map` 是合法调用。"""
    src = _books_csv(tmp_path / "in.csv")
    result = execute(str(src), None, sorts=["价格:desc"], dry_run=True)
    assert result["maps"] == []
    assert result["post_processed"] is True
    assert result["post_processing"]["sort_keys"] == ["价格 desc"]


def test_no_operation_at_all_is_rejected(tmp_path: Path) -> None:
    src = _books_csv(tmp_path / "in.csv")
    with pytest.raises(ValueError, match="至少一个操作"):
        execute(str(src), None)


def test_zero_rows_is_an_error_not_an_empty_deliverable(tmp_path: Path) -> None:
    """枚举为空即报错：0 行时既排不出东西、也校验不了列名，静默出空文件会被当成"跑成功了"。"""
    src = tmp_path / "empty.csv"
    src.write_text("分类,价格\n", encoding="utf-8")
    with pytest.raises(ValueError, match="0 行"):
        execute(str(src), str(tmp_path / "out.csv"), group_by=["分类"], aggregations=["count"])


# ── 回执：口径必须可见 ──────────────────────────────────────────────────


def test_receipt_records_partition_and_kind(tmp_path: Path) -> None:
    src = _books_csv(tmp_path / "in.csv")
    result = execute(
        str(src),
        None,
        maps=["价格 = parse_money(价格)"],
        group_by=["分类"],
        aggregations=["count", "sum(价格_parsed):总价", "avg(价格_parsed):均价"],
        sorts=["总价:desc"],
        dry_run=True,
    )
    post = result["post_processing"]
    assert post["rows_in"] == 6
    assert post["rows_out"] == 3
    assert post["groups"] == 3
    assert post["grouped_rows"] == 6
    assert post["partition_ok"] is True
    assert post["sorted_as"] == {"总价": "numeric"}
    # 文本被按数值解读过 ⇒ 必须说出来
    assert post["numeric_from_text_columns"] == ["价格_parsed"]
    assert post["non_numeric_cells"] == 0
    # 交付物就是聚合表，不是"变换涉及的列"切片
    assert result["preview"][0]["分类"] == "小说"
    assert result["preview"][0]["总价"] == 155.61
    assert result["preview"][0]["均价"] == 51.87


def test_summary_marks_post_processing(tmp_path: Path) -> None:
    """★ `to_dict` 漏掉计算属性 `post_processed` 时，这条摘要会静默消失（实测踩过）。"""
    src = _books_csv(tmp_path / "in.csv")
    result = execute(
        str(src),
        None,
        maps=["价格 = parse_money(价格)"],
        group_by=["分类"],
        aggregations=["count", "sum(价格_parsed):总价"],
        sorts=["总价:desc"],
        dry_run=True,
    )
    summary = result["post_processing_summary"]
    assert "本次含后处理" in summary
    assert "按 分类 分组" in summary
    assert "排序 总价（数值口径）" in summary
    assert "输入 6 行 → 交付 3 行" in summary


def test_summary_reports_text_sort_kind(tmp_path: Path) -> None:
    src = _books_csv(tmp_path / "in.csv")
    result = execute(str(src), None, sorts=["价格:desc"], dry_run=True)
    assert "价格（文本口径）" in result["post_processing_summary"]


# ── 不静默：取不到数值必须落到终端提示上 ────────────────────────────────


def test_unreadable_aggregation_emits_copyable_advice(tmp_path: Path) -> None:
    src = _books_csv(tmp_path / "in.csv")
    result = execute(
        str(src),
        None,
        group_by=["分类"],
        aggregations=["count", "sum(价格):总价"],
        dry_run=True,
    )
    post = result["post_processing"]
    assert post["non_numeric_cells"] == 6
    assert post["non_numeric_columns"] == ["价格"]
    assert post["conversion_recipes"] == [
        {"source": "价格", "function": "parse_money", "target": "价格_parsed"}
    ]
    notice = result["notice_numeric_text"]
    assert '--map "价格 = parse_money(价格)"' in notice
    assert "价格_parsed" in notice
    assert "无法对 sum/avg 求和" in notice
    assert "6 个取值取不到数值" in result["post_processing_summary"]


def test_advice_switches_function_instead_of_stacking_suffix(tmp_path: Path) -> None:
    """★ 用户换错函数（对货币列用 parse_number）时，建议必须"换函数对源列重做"，
    而不是 `--map "价格_parsed = parse_money(价格_parsed)"`（目标列成了 价格_parsed_parsed）。"""
    src = _books_csv(tmp_path / "in.csv")
    result = execute(
        str(src),
        None,
        maps=["价格 = parse_number(价格)"],
        group_by=["分类"],
        aggregations=["sum(价格_parsed):总价"],
        dry_run=True,
    )
    notice = result["notice_numeric_text"]
    assert '--map "价格 = parse_money(价格)"' in notice
    assert "_parsed_parsed" not in notice


def test_unconvertible_column_gets_no_invented_command(tmp_path: Path) -> None:
    src = _books_csv(tmp_path / "in.csv")
    result = execute(str(src), None, aggregations=["sum(标题)"], dry_run=True)
    post = result["post_processing"]
    assert post["unconvertible_columns"] == ["标题"]
    assert post["conversion_recipes"] == []
    notice = result["notice_numeric_text"]
    assert "无法自动转换" in notice
    assert "--map" not in notice


def test_no_notice_when_nothing_is_wrong(tmp_path: Path) -> None:
    src = _books_csv(tmp_path / "in.csv")
    result = execute(
        str(src),
        None,
        maps=["价格 = parse_money(价格)"],
        group_by=["分类"],
        aggregations=["sum(价格_parsed):总价"],
        dry_run=True,
    )
    assert "notice_numeric_text" not in result


# ── 落盘：交付物本身 ────────────────────────────────────────────────────


def test_confirm_writes_the_aggregate_table(tmp_path: Path) -> None:
    src = _books_csv(tmp_path / "in.csv")
    dst = tmp_path / "grouped.csv"
    result = execute(
        str(src),
        str(dst),
        maps=["价格 = parse_money(价格)"],
        group_by=["分类"],
        aggregations=["count", "sum(价格_parsed):总价", "max(标题):示例"],
        sorts=["总价:desc"],
        confirm=True,
    )
    assert result["mode"] == "write"
    assert result["written"] is True
    rows = _read_csv(dst)
    assert [row["分类"] for row in rows] == ["小说", "科普", "诗歌"]  # 按总价 desc
    assert [row["总价"] for row in rows] == ["155.61", "32.99", "8.5"]
    assert [row["示例"] for row in rows] == ["Tipping the Velvet", "Sharp Objects", "The Black Maria"]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    src = _books_csv(tmp_path / "in.csv")
    dst = tmp_path / "never.csv"
    result = execute(str(src), str(dst), sorts=["价格:desc"], dry_run=True)
    assert result["written"] is False
    assert not dst.exists()


def test_write_without_target_is_rejected(tmp_path: Path) -> None:
    src = _books_csv(tmp_path / "in.csv")
    with pytest.raises(ValueError, match="目标文件"):
        execute(str(src), None, sorts=["价格:desc"], confirm=True)
