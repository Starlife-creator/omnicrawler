"""`runtime/stdlib_bundle.py` 的守卫：冻结便携包的标准库隐藏导入清单。

## 为什么这个模块值得测（此前 0% 覆盖）

`stdlib_hiddenimports()` 决定冻结便携包里 `base_library.zip` / `PYZ`
**保留哪些标准库模块**。PyInstaller 默认按静态导入图裁剪 —— 而外部
Wheel-Pack 组件（在宿主之外运行）用它自己的一套 `import`，宿主的静态
分析看不见。

清单缺一个模块的后果不是「测试失败」，而是**用户在便携包里装了组件、
组件却报 `ModuleNotFoundError`** —— 而本机开发环境（有完整标准库）
永远复现不出来。2026-11-23 用 `coverage report --sort` 发现它 0% 覆盖。

## 判据（都与「确定性」有关）

1. 结果非空且是排序的（下游直接序列化进 spec 文件，顺序必须稳定）；
2. 关键模块必须在列（`ssl` / `asyncio` / `json` 等是组件高频依赖）；
3. 排除清单真的被排除（`EXCLUDED_STDLIB_MODULES` 一条都不许漏进来）；
4. 私有模块（下划线开头）不进清单；
5. **反向断言**：把某个模块加进排除清单 ⇒ 它必须从结果里消失。

## 边界

它验的是「清单生成逻辑正确」，**不验**「PyInstaller 真的把它打进去了」——
那要靠便携包冒烟（CI 的 `portable-smoke`）。
"""
from __future__ import annotations

import sys

import pytest

from omnicrawler.runtime.stdlib_bundle import (
    EXCLUDED_STDLIB_MODULES,
    stdlib_hiddenimports,
)


def test_result_is_non_empty_and_sorted() -> None:
    """清单必须非空（空清单 = 便携包里没有任何标准库）且有序。"""
    names = stdlib_hiddenimports()
    assert names, "标准库隐藏导入清单为空 —— 便携包里的组件将无法 import 任何标准库"
    assert names == sorted(names), "清单未排序：下游会把它原样写进 spec，顺序必须稳定"
    assert len(set(names)) == len(names), "清单有重复项"


@pytest.mark.parametrize(
    "required",
    [
        "ssl",        # 组件访问 https 必需
        "json",       # 组件协议输出走 JSON
        "asyncio",    # 异步采集器
        "sqlite3",    # 状态库
        "hashlib",    # 哈希/签名校验
        "typing",     # 大量组件的模块级注解
    ],
)
def test_critical_modules_are_present(required: str) -> None:
    """这些是组件高频依赖，缺一个就会在便携包里炸。"""
    names = stdlib_hiddenimports()
    assert required in names, (
        f"关键标准库模块 {required!r} 不在隐藏导入清单里 —— "
        "外部 Wheel-Pack 组件在冻结包里会 ModuleNotFoundError"
    )


def test_excluded_modules_never_leak_in() -> None:
    """排除清单里的模块一条都不许进结果。"""
    names = set(stdlib_hiddenimports())
    leaked = sorted(names & set(EXCLUDED_STDLIB_MODULES))
    assert not leaked, f"这些被排除的模块仍出现在清单里：{leaked}"


def test_private_modules_are_not_included() -> None:
    """下划线开头的私有模块不进清单（它们不该被组件直接 import）。"""
    names = stdlib_hiddenimports()
    private = [name for name in names if name.startswith("_")]
    # `_ssl` 是**刻意**加入的例外（见模块里的 `names: set[str] = {"ssl", "_ssl"}`）。
    assert private in ([], ["_ssl"]), f"意外引入了私有模块：{private}"


def test_reverse_assertion_excluding_a_module_removes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**反向断言**：把某模块加进排除清单 ⇒ 它必须从结果里消失。

    这条保证「排除清单真的在起作用」，而不只是被读了一下。
    若把 `EXCLUDED_STDLIB_MODULES` 的过滤条件删掉，本用例会红。
    """
    names = set(stdlib_hiddenimports())
    target = next((n for n in ("sqlite3", "json", "typing") if n in names), "")
    if not target:
        pytest.skip("找不到可用于反向断言的候选模块")

    monkeypatch.setattr(
        "omnicrawler.runtime.stdlib_bundle.EXCLUDED_STDLIB_MODULES",
        frozenset(EXCLUDED_STDLIB_MODULES) | {target},
    )
    after = set(stdlib_hiddenimports())
    assert target not in after, (
        f"把 {target!r} 加进排除清单后它仍在结果里 —— 排除逻辑没有真正生效"
    )


def test_stdlib_module_names_is_available_on_this_interpreter() -> None:
    """`sys.stdlib_module_names` 是清单的来源，缺它清单会退化为空。

    它从 3.10 起提供；本项目要求 >=3.13，所以不该缺 —— 但显式断言一次，
    免得将来降级运行时后静默产出空清单。
    """
    assert hasattr(sys, "stdlib_module_names"), (
        "当前解释器没有 sys.stdlib_module_names，隐藏导入清单会退化为空"
    )
    assert sys.stdlib_module_names, "sys.stdlib_module_names 为空"
