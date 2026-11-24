"""`format_bytes`：体积读数是「上限判断」的依据，必须向下取整。

为什么要有这个文件：四舍五入会给出**乐观**读数，而用户拿它判断
「装不装得下 / 超没超限额」。

实测（2026-11-24）修复前：

```
1_048_575 bytes（1 MiB - 1）  ->  "1 MiB"       # 说大了（实际不到 1 MiB）
1_073_741_823 bytes（1 GiB-1） ->  "1024 MiB"    # 过不了单位切换，读数像另一个量级
```

同一条纪律在 `commands/self_update._human_bytes` 已经执行过（第 5 轮）；
本文件把 `services/component_install.format_bytes` 也钉住。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.component_install import format_bytes

MIB = 1024 * 1024
GIB = 1024 * MIB


def test_just_under_one_mib_is_not_rounded_up() -> None:
    """1 MiB - 1 字节显示成 0 MiB，而不是 1 MiB。"""
    assert format_bytes(MIB - 1) == "0 MiB"


def test_exactly_one_mib() -> None:
    assert format_bytes(MIB) == "1 MiB"


def test_one_and_a_half_mib_truncates() -> None:
    """1.5 MiB 截断为 1 MiB —— 截断的语义是「至少这么大」。"""
    assert format_bytes(MIB * 3 // 2) == "1 MiB"


def test_just_under_one_gib_stays_in_mib() -> None:
    """1 GiB - 1 不得显示成 1024 MiB（那会看着像另一个量级）。"""
    assert format_bytes(GIB - 1) == "1023 MiB"


def test_exactly_one_gib_switches_unit() -> None:
    assert format_bytes(GIB) == "1.0 GiB"


def test_gib_fraction_truncates() -> None:
    """4.79 GiB 不许显示成 4.8 GiB。"""
    assert format_bytes(int(GIB * 4.79)) == "4.7 GiB"


@pytest.mark.parametrize("value", [0, 1, 1000, MIB // 2])
def test_tiny_values_are_zero_mib(value: int) -> None:
    """小于 1 MiB 一律 0 MiB —— 诚实，不虚报。"""
    assert format_bytes(value) == "0 MiB"


def test_output_never_exceeds_the_real_value() -> None:
    """核心不变式：显示值换算回字节后不得超过真实值。"""
    for value in (MIB - 1, MIB, 3 * MIB // 2, GIB - 1, GIB, 5 * GIB + 12345):
        text = format_bytes(value)
        number, unit = text.split()
        scale = {"MiB": MIB, "GiB": GIB}[unit]
        shown = float(number) * scale
        assert shown <= value, f"{value} 显示成 {text}，超出真实值"
