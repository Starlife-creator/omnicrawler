"""ConvertX 路径枚举（convertx/paths.py）单元测试。

覆盖：族目录完整性、全路径枚举、核心格式可用性、META 覆盖层
（同族禁用 / jsonl→jsonl 显式开启 / note）、可选依赖路径的
enabled 与注册表一致、path_matrix / describe 视图。
"""

from __future__ import annotations

from omnicrawler.convertx.paths import (
    available_paths,
    describe,
    enumerate_paths,
    format_families,
    path_matrix,
)


def test_format_families_catalog() -> None:
    families = format_families()
    assert set(families) == {"csv", "jsonl", "xlsx", "parquet", "duckdb", "document"}


def test_enumerate_covers_full_cartesian_product() -> None:
    paths = enumerate_paths()
    n = len(format_families())
    assert len(paths) == n * n  # n 族 × n 族
    assert {p.src_family for p in paths} == set(format_families())
    assert {p.dst_family for p in paths} == set(format_families())


def test_core_formats_enabled_without_optional_deps() -> None:
    by_key = {(p.src_family, p.dst_family): p for p in enumerate_paths()}
    # CSV/JSONL 为核心格式，无可选依赖，双向必可用
    assert by_key[("csv", "jsonl")].enabled is True
    assert by_key[("jsonl", "csv")].enabled is True
    assert by_key[("csv", "csv")].enabled is False  # 同族默认禁用


def test_jsonl_to_jsonl_enabled_by_meta() -> None:
    by_key = {(p.src_family, p.dst_family): p for p in enumerate_paths()}
    path = by_key[("jsonl", "jsonl")]
    assert path.enabled is True  # META 显式开启
    assert "重排" in path.label
    assert "flat" in path.note


def test_meta_note_overrides_default_hints() -> None:
    by_key = {(p.src_family, p.dst_family): p for p in enumerate_paths()}
    assert "同格式拷贝" in by_key[("csv", "csv")].note


def test_optional_dep_paths_reflect_registry() -> None:
    from omnicrawler.convertx import READERS, WRITERS

    by_key = {(p.src_family, p.dst_family): p for p in enumerate_paths()}
    for family, exts in {"xlsx": (".xlsx",), "parquet": (".parquet",), "duckdb": (".duckdb",)}.items():
        reader_ok = any(ext in READERS for ext in exts)
        writer_ok = any(ext in WRITERS for ext in exts)
        # 路径始终出现在枚举中；enabled 必须与注册表状态一致
        assert by_key[(family, "csv")].enabled is reader_ok
        assert by_key[("csv", family)].enabled is writer_ok
        if not reader_ok:
            assert "需要" in by_key[(family, "csv")].note
        if not writer_ok:
            assert "需要" in by_key[("csv", family)].note


def test_available_paths_enabled_only() -> None:
    paths = available_paths()
    assert paths
    assert all(path.enabled for path in paths)
    all_paths = available_paths(enabled_only=False)
    assert len(all_paths) == len(format_families()) ** 2


def test_path_matrix_view() -> None:
    from omnicrawler.convertx.paths import format_families

    matrix = path_matrix()
    assert "csv" in matrix
    assert "jsonl" in matrix["csv"]  # 核心路径必在
    # 目标顺序与族目录一致（稳定、无重复、无未知族）
    order = format_families()
    for targets in matrix.values():
        assert targets == [family for family in order if family in targets]
        assert len(targets) == len(set(targets))


def test_describe_text() -> None:
    text = describe()
    assert "ConvertX 转换路径" in text
    assert "csv → jsonl" in text
    assert text.count("\n") >= 25  # 全部路径都在清单里
