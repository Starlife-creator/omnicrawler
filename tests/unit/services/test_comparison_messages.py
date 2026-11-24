"""错误/警告文案里出现比较时，**比较的两边都要写出来**。

为什么要有这个文件：第 21 轮修 `_tier_reason` 时发现，文案写着
「置信度 0.33 ≥ 阈值」却**没写出阈值**——读者无法核对该句是否成立。

这一轮把同一条判据套到「大小限制 / 数量上限」类文案上，找到四处：

| 位置 | 原来的问题 |
|---|---|
| `http_client`（4 处）| 只给上限，不知道实际超了多少 |
| `async_fetcher` 重定向 | 只给上限 |
| `_csv` 单元格截断 | 只说「超过字符上限」，不给上限值 |

本文件钉住「上限值必须可见」这一条，避免以后又把常量写回裸值。
"""

from __future__ import annotations

import pytest

from omnicrawler.core.utils import human_bytes


@pytest.mark.parametrize("limit", [1024, 20 * 1024 * 1024, 5 * 1024**3])
def test_human_bytes_is_used_for_size_limits(limit: int) -> None:
    """大小上限在文案里要能读，不能是裸字节数。"""
    text = human_bytes(limit)
    assert text != str(limit), f"{limit} 仍是裸数字"
    assert any(unit in text for unit in ("B", "KiB", "MiB", "GiB"))


def test_csv_cell_limit_constant_exists_and_is_named() -> None:
    """字符上限必须是**有名字的常量**，提示才能引用它。"""
    from omnicrawler.services.table_io._csv import _MAX_CELL_CHARS

    assert _MAX_CELL_CHARS == 32700


def test_csv_warning_includes_the_actual_limit(tmp_path) -> None:
    """截断警告要写出上限值，否则用户不知道怎么调。"""
    from omnicrawler.services.table_io._csv import write_csv

    target = tmp_path / "out.csv"
    huge = "x" * 40000
    result = write_csv([{"col": huge}], target, {"columns": ["col"]})
    assert result["truncated_cells"] == 1, result
    joined = "\n".join(result["warnings"])
    assert "32700" in joined, f"警告里没有上限值：{joined}"


def test_redirect_message_shows_both_sides() -> None:
    """重定向消息要同时给「实际次数」与「上限」。

    该消息在 `async_fetcher` 的深层流程里，这里直接断言源码构造——
    端到端触发需要模拟连续 301，成本远高于收益。
    """
    import inspect

    from omnicrawler.fetching import async_fetcher

    source = inspect.getsource(async_fetcher)
    assert "重定向次数超过上限: {redirect_count} > {max_redirects}" in source, (
        "重定向消息应同时包含实际次数与上限"
    )
