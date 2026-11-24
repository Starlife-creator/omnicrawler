"""两份 `_human_bytes` 必须同口径 —— docstring 声称同口径，实现曾不同步。

为什么要有这个文件：`gui/update_check._human_bytes` 的 docstring 写着
「与 commands.self_update._human_bytes 同口径（那里不 import GUI，这里独立实现）」。
但第 5 轮把 `self_update` 改成**向下取整**后，**没有同步改这一份** ——
docstring 说的和实际做的对不上。

实测（2026-11-24）修复前：

```
5_143_223_336 bytes（4.79 GiB）  self_update: 4.7GB   update_check: 4.8GB   ← 不一致
1_073_741_823 bytes（1 GiB-1）   self_update: 1023.9MB  update_check: 1024.0MB
```

更新包大小是用户判断「下多久 / 够不够空间」的读数，四舍五入给的是乐观答案。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtCore")

from omnicrawler.commands.self_update import _human_bytes as cli_human_bytes  # noqa: E402
from omnicrawler.gui.update_check import _human_bytes as gui_human_bytes  # noqa: E402

MIB = 1024 * 1024
GIB = 1024 * MIB


@pytest.mark.parametrize(
    "size",
    [
        MIB - 1,
        MIB,
        MIB * 3 // 2,
        GIB - 1,
        GIB,
        int(GIB * 4.79),
        5_143_223_336,
        0,
        512,
    ],
)
def test_both_implementations_agree(size: int) -> None:
    """逐字节一致 —— docstring 就是这么承诺的。"""
    assert gui_human_bytes(size) == cli_human_bytes(size), (
        f"{size} 字节：CLI 显示 {cli_human_bytes(size)}，GUI 显示 {gui_human_bytes(size)}"
    )


def test_gui_does_not_round_up_at_a_unit_boundary() -> None:
    """1 GiB - 1 不得显示成 1024.0MB（那会让读数跨过单位）。"""
    assert gui_human_bytes(GIB - 1) != "1024.0MB"
    assert gui_human_bytes(GIB - 1).endswith("MB")


def test_gui_output_never_exceeds_the_real_value() -> None:
    """核心不变式：显示值换算回字节后不得超过真实值。"""
    for size in (MIB - 1, GIB - 1, 5_143_223_336):
        text = gui_human_bytes(size)
        number = float(text.rstrip("BKMG"))
        unit = text[len(text.rstrip("BKMG")):]
        scale = {"B": 1, "KB": 1024, "MB": MIB, "GB": GIB}[unit]
        assert number * scale <= size, f"{size} 显示成 {text}，超出真实值"
