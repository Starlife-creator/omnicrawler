"""Python 标准库隐藏导入清单（供冻结便携包为外部 Wheel-Pack 子进程提供完整标准库底座）。

为什么必须由单点维护：
- PyInstaller 默认按静态导入图裁剪 `base_library.zip` / `PYZ` 中的标准库模块；
- 当用户在冻结包外通过声明式 Wheel-Pack（如 `ocr-paddle`）把 `paddleocr` / `scipy` 等
  安装到 `.omnicrawler/components/<id>/site-packages/` 并由 `omnicrawler-worker`
  隔离子进程加载时，若宿主裁剪了冷门标准库（如 `unittest`、`xmlrpc`、`wsgiref`、
  `sqlite3`、`sysconfig`），子进程会在导入阶段报 `ModuleNotFoundError`；
- 同时必须显式剔除 `test` / `tkinter` / `idlelib` / `turtledemo` / `venv` / `ensurepip`
  等测试或 Tk GUI 套件，防止无关系统库被卷入便携包。
"""

from __future__ import annotations

import importlib.util
import pkgutil
import sys
from collections.abc import Iterable

#: 明确不打入便携包的标准库顶层模块（测试夹具、Tk/GUI 玩具、pip 引导器等）。
EXCLUDED_STDLIB_MODULES: frozenset[str] = frozenset(
    {
        "antigravity",
        "curses",
        "dbm",
        "ensurepip",
        "idlelib",
        "lib2to3",
        "pydoc_data",
        "test",
        "this",
        "tkinter",
        "turtle",
        "turtledemo",
        "venv",
    }
)

#: 含子模块且常被科学计算/网络/数据类 Wheel 间接引用的标准库包。
_STDLIB_SUBPACKAGE_ROOTS: tuple[str, ...] = (
    "asyncio",
    "concurrent",
    "ctypes",
    "email",
    "html",
    "http",
    "importlib",
    "logging",
    "multiprocessing",
    "sqlite3",
    "unittest",
    "urllib",
    "wsgiref",
    "xml",
    "xmlrpc",
)


def _safe_find_spec(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _collect_stdlib_package_submodules(package_name: str) -> list[str]:
    try:
        spec = importlib.util.find_spec(package_name)
    except (ImportError, ValueError, AttributeError):
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    found: list[str] = []
    for info in pkgutil.walk_packages(spec.submodule_search_locations, prefix=f"{package_name}."):
        leaf = info.name.rsplit(".", 1)[-1]
        if leaf.startswith("_") or leaf == "test" or leaf.startswith("test_"):
            continue
        if ".test." in info.name or ".tests." in info.name:
            continue
        found.append(info.name)
    return found


def stdlib_hiddenimports() -> list[str]:
    """返回当前平台可用的公开标准库模块与关键子模块有序列表。"""
    names: set[str] = {"ssl", "_ssl"}
    raw_stdlib: Iterable[str] = getattr(sys, "stdlib_module_names", frozenset())
    for mod in raw_stdlib:
        if not isinstance(mod, str) or not mod or mod.startswith("_"):
            continue
        if mod in EXCLUDED_STDLIB_MODULES:
            continue
        if _safe_find_spec(mod):
            names.add(mod)

    for pkg in _STDLIB_SUBPACKAGE_ROOTS:
        if pkg in names:
            names.update(_collect_stdlib_package_submodules(pkg))

    return sorted(names)
