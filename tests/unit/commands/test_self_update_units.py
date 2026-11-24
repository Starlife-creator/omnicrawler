"""self_update 的纯函数单元：体积格式化与变更包成员核对。

为什么要有这个文件：`commands/self_update.py` 是**覆盖率最低的核心模块**
（13%），而它负责替换程序本体。这两个函数不需要网络、不需要真实安装树，
却是用户直接读到 / 直接决定「能不能更新成功」的那一层。

实测缺陷（2026-11-24）：`_human_bytes` 用四舍五入，于是
`2 GiB - 1` 显示成 `2.0GB` —— 而 GitHub Release 的单资产上限**正好**是 2 GiB。
用户据此判断「没超限吧」会得到相反结论；`1048575` 也曾显示成 `1024.0KB`。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from omnicrawler.commands.self_update import _delta_missing_members, _human_bytes


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0B"),
        (1, "1B"),
        (1023, "1023B"),
        (1024, "1.0KB"),
        (1536, "1.5KB"),
        (1048576, "1.0MB"),
        (1073741824, "1.0GB"),
    ],
)
def test_human_bytes_basic_units(size: int, expected: str) -> None:
    assert _human_bytes(size) == expected


def test_human_bytes_truncates_instead_of_rounding_up() -> None:
    """**不得**四舍五入到下一个单位：那会给出乐观读数。

    `2 GiB - 1` 与 `2 GiB` 只差 1 字节，但前者**没到** GitHub 的资产上限。
    四舍五入会让两者都显示 `2.0GB`，用户无从区分。
    """
    assert _human_bytes(2 * 1024**3 - 1) != _human_bytes(2 * 1024**3)
    assert _human_bytes(2 * 1024**3 - 1) == "1.9GB"
    assert _human_bytes(1048575) != "1024.0KB"
    assert _human_bytes(1048575) == "1023.9KB"


def test_human_bytes_is_monotonic_across_unit_boundaries() -> None:
    """跨单位边界也必须单调不减 —— 这是「看起来对」之外的可验证性质。"""
    boundaries = [1024**k for k in range(1, 4)]
    for boundary in boundaries:
        below = _human_bytes(boundary - 1)
        at = _human_bytes(boundary)
        assert below != at, f"{boundary - 1} 与 {boundary} 显示相同：{at}"


def _delta(tmp_path: Path, members: list[str]) -> Path:
    archive = tmp_path / "delta.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in members:
            zf.writestr(name, "x")
    return archive


def test_delta_missing_members_reports_absent_ones(tmp_path: Path) -> None:
    archive = _delta(tmp_path, ["a.py", "b/c.py"])
    expected = {"a.py": "s1", "b/c.py": "s2", "d.py": "s3"}
    assert _delta_missing_members(archive, expected) == ["d.py"]


def test_delta_missing_members_is_empty_when_all_present(tmp_path: Path) -> None:
    archive = _delta(tmp_path, ["a.py"])
    assert _delta_missing_members(archive, {"a.py": "s1"}) == []


def test_delta_missing_members_is_sorted(tmp_path: Path) -> None:
    archive = _delta(tmp_path, [])
    expected = {"z.py": "1", "a.py": "2", "m/x.py": "3"}
    assert _delta_missing_members(archive, expected) == ["a.py", "m/x.py", "z.py"]
