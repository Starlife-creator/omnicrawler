"""A-P3-1/A-P3-4：工作区大小缓存容量上限 + mtime 指纹复用契约回归测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.runtime import resources


def test_size_cache_evicts_oldest_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(resources, "_SIZE_CACHE_MAX_ROOTS", 3)
    resources._SIZE_CACHE.clear()
    for index in range(6):
        root = tmp_path / f"root-{index}"
        root.mkdir()
        (root / "file.txt").write_text("x", encoding="utf-8")
        resources._directory_size(root)
    assert len(resources._SIZE_CACHE) == 3
    oldest = tmp_path / "root-0"
    assert oldest not in resources._SIZE_CACHE
    assert tmp_path / "root-5" in resources._SIZE_CACHE


def test_mtime_fingerprint_hit_reuses_cached_size(tmp_path: Path) -> None:
    resources._SIZE_CACHE.clear()
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "report.md"
    target.write_text("hello", encoding="utf-8")

    first = resources._directory_size(root)
    cached_entry = resources._SIZE_CACHE[root]
    assert first == cached_entry[0]

    # 指纹未变：第二次直接复用缓存大小，不再全量 stat。
    second = resources._directory_size(root)
    assert second == first
    assert resources._SIZE_CACHE[root] == cached_entry

    # 新增条目改变目录指纹（文件计数 +1）：大小重新计算并回填缓存。
    (root / "extra.txt").write_text("aa", encoding="utf-8")
    third = resources._directory_size(root)
    assert third > first
    assert resources._SIZE_CACHE[root][0] == third
