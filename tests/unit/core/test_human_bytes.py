"""`core.utils.human_bytes`：限额类错误消息要给人看得懂的读数。

为什么要有这个文件：`fetching` 层的「超过大小限制」错误此前直接打印裸字节数：

```
响应超过大小限制: 21000000 > 20971520
```

用户要自己算才知道上限是 20 MiB。之所以放在 `core` 而不是复用
`services.component_install.format_bytes` 或 `commands.self_update._human_bytes`：
`fetching` 不能依赖 `services` / `commands`（会形成反向依赖）。

本文件钉住三条：单位正确（二进制）、**向下取整**、边界不跨档。
"""

from __future__ import annotations

from omnicrawler.core.utils import human_bytes

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB


def test_uses_binary_units() -> None:
    """限额都以 1024 为基数，写成 `MB` 会让人按 1000 换算（差 4.8%）。"""
    assert human_bytes(MIB) == "1.0 MiB"
    assert human_bytes(GIB) == "1.0 GiB"
    assert human_bytes(KIB) == "1.0 KiB"


def test_plain_bytes_have_no_decimals() -> None:
    """`0 B` / `1 B` 不该显示成 `0.0 B`。"""
    assert human_bytes(0) == "0 B"
    assert human_bytes(1) == "1 B"
    assert human_bytes(1023) == "1023 B"


def test_truncates_instead_of_rounding() -> None:
    """`1 GiB - 1` 不许显示成 `1.0 GiB`（那是乐观答案）。"""
    assert human_bytes(GIB - 1) == "1023.9 MiB"
    assert human_bytes(MIB - 1) == "1023.9 KiB"


def test_truncates_within_a_unit() -> None:
    """4.79 GiB 显示 4.7 GiB，不是 4.8 GiB。"""
    assert human_bytes(int(GIB * 4.79)) == "4.7 GiB"


def test_the_20_mib_case_from_the_real_message() -> None:
    """真实场景：默认上限 20 MiB 要显示成人能读的样子。"""
    assert human_bytes(20 * MIB) == "20.0 MiB"


def test_output_never_exceeds_the_real_value() -> None:
    """核心不变式：显示值换算回字节后不得超过真实值。"""
    for size in (1, 1023, KIB - 1, KIB, MIB - 1, MIB, GIB - 1, GIB, int(GIB * 4.79)):
        text = human_bytes(size)
        number, unit = text.split() if " " in text else (text, "B")
        scale = {"B": 1, "KiB": KIB, "MiB": MIB, "GiB": GIB}[unit]
        assert float(number) * scale <= size, f"{size} 显示成 {text}，超出真实值"


def test_accepts_float_input() -> None:
    """调用方可能传 float（如 `usage.free`），不能抛。"""
    assert human_bytes(float(MIB)) == "1.0 MiB"
