"""mypy 严格范围（ratchet）：只能扩大，不能悄悄缩小。

起因（2026-09-11）：GUI 的 mypy 严格化原计划按
`widgets/ → views/ → wizard/ → runner/ → main/ → delegates/` 逐段推进，
配置里为此长期挂着「core 严格、其余 GUI 宽松」的两段 override。
实测后一次性收紧到**全 gui 严格**（严格档下只余 104 处，其中 37 处同根因）。
但配置是「后者覆盖前者」的列表语义——**只要有人在后面追加一条更宽松的 override，
范围就会无声缩水**，而 mypy 依然「通过」。本文件把这件事变成断言。

2026-09-15（W6.3）：范围再扩到 **`omnicrawler.core.*`**（实测严格档下仅 6 处违规，
同根因：`cryptography` 可选 ⇒ 类型层面是 Any）。同时把本守卫**泛化**——
不再只枚举 gui，而是按 `_REQUIRED_STRICT_SCOPE` 里的每个模式枚举磁盘上的**实际模块**，
所以新加 core 文件会自动纳入检查。

**怎么验算**：不复述配置文本，而是按 mypy 的匹配语义（`foo.*` 匹配 `foo` 及其子模块，
取**最后一条**命中的 override）对**磁盘上实际存在的每个受管模块**逐一算出生效规则，
再断言 5 项严格设置全部为真。因此：

* 新增受管模块 → 自动纳入检查范围（新文件必须也是严格的）；
* 追加更宽松的 override → 命中它的模块算出的规则不严格 → 失败；
* 把整块严格设置删掉 → 失败。

**边界（如实说明）**：这里断言的是**配置语义**，不替代 mypy 本身；
「严格档下确实零违规」由 `mypy src/omnicrawler` 门禁保证。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_SRC_ROOT = _REPO_ROOT / "src"

#: 严格档定义（沿用原 gui/core 的 Phase 2 口径）。少一项都算「不严格」。
_STRICT_SETTINGS = (
    "disallow_untyped_defs",
    "disallow_incomplete_defs",
    "check_untyped_defs",
    "warn_return_any",
    "warn_unused_ignores",
)

#: ratchet：严格范围**至少要**覆盖这些模式。只能在此之上增加。
_REQUIRED_STRICT_SCOPE = (
    "omnicrawler.gui.*",
    "omnicrawler.core.*",
)

#: 每个受管包的模块数下限（防止"扫到空集 ⇒ 空集对空集假通过"）
_MIN_MODULES_PER_SCOPE = {
    "omnicrawler.gui.*": 50,
    "omnicrawler.core.*": 10,
}


def _overrides() -> list[dict[str, object]]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return list(data["tool"]["mypy"]["overrides"])


def _matches(pattern: str, module: str) -> bool:
    """复刻 mypy 的 module 匹配：`foo.*` 命中 `foo` 与 `foo.*`；其余为精确匹配。"""
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return module == prefix or module.startswith(prefix + ".")
    return module == pattern


def _effective(module: str) -> dict[str, object]:
    """取**最后一条**命中该模块的 override（mypy 是后者覆盖前者）。"""
    chosen: dict[str, object] = {}
    for override in _overrides():
        pattern = str(override.get("module", ""))
        if pattern and _matches(pattern, module):
            chosen.update(override)
    return chosen


def _package_root(pattern: str) -> Path:
    """`omnicrawler.gui.*` → `src/omnicrawler/gui`。"""
    dotted = pattern[:-2] if pattern.endswith(".*") else pattern
    return _SRC_ROOT.joinpath(*dotted.split("."))


def _package_modules(pattern: str) -> list[str]:
    """磁盘上该包的实际模块名（含包本身与所有子模块）。"""
    root = _package_root(pattern)
    if not root.is_dir():
        return []
    dotted = pattern[:-2] if pattern.endswith(".*") else pattern
    modules = [dotted]
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if not parts:
            continue
        modules.append(dotted + "." + ".".join(parts))
    return modules


def _required_modules() -> list[str]:
    found: list[str] = []
    for pattern in _REQUIRED_STRICT_SCOPE:
        found.extend(_package_modules(pattern))
    return sorted(set(found))


def test_required_scope_roots_exist() -> None:
    """先确认每个受管包都扫到了东西——否则下面的断言会变成空集对空集的假通过。"""
    for pattern in _REQUIRED_STRICT_SCOPE:
        root = _package_root(pattern)
        assert root.is_dir(), f"未找到受管源码目录：{root}"
        count = len(_package_modules(pattern))
        floor = _MIN_MODULES_PER_SCOPE.get(pattern, 1)
        assert count >= floor, f"{pattern} 只扫到 {count} 个模块（<{floor}），路径推断可能写错了"


def test_required_strict_scope_is_declared() -> None:
    """ratchet：要求的严格范围必须在配置里声明出来。"""
    patterns = {str(o.get("module", "")) for o in _overrides()}
    missing = [p for p in _REQUIRED_STRICT_SCOPE if p not in patterns]
    assert not missing, (
        f"严格范围缺少声明：{missing}。若确实要缩小范围，请先修改本测试里的 _REQUIRED_STRICT_SCOPE "
        f"并说明理由——范围缩小不该是「顺手」发生的。"
    )


@pytest.mark.parametrize("module", _required_modules())
def test_every_required_module_is_strict(module: str) -> None:
    """每一个受管模块的**生效**规则都必须是严格档。"""
    settings = _effective(module)
    relaxed = [name for name in _STRICT_SETTINGS if settings.get(name) is not True]
    assert not relaxed, (
        f"{module} 的生效 mypy 规则不严格：{relaxed} 未开启。"
        f"生效值={ {n: settings.get(n) for n in _STRICT_SETTINGS} }。"
        f"常见成因：在严格 override **之后**又追加了一条更宽松的同前缀 override。"
    )


@pytest.mark.parametrize("pattern", _REQUIRED_STRICT_SCOPE)
def test_no_relaxed_override_after_strict_scope(pattern: str) -> None:
    """反面守卫：每个严格 scope 之后不得再出现会放宽其模块的 override。"""
    overrides = _overrides()
    strict_index = None
    for index, override in enumerate(overrides):
        if str(override.get("module", "")) == pattern and all(
            override.get(name) is True for name in _STRICT_SETTINGS
        ):
            strict_index = index
    assert strict_index is not None, f"没找到 `{pattern}` 的严格 override"
    prefix = pattern[:-2] if pattern.endswith(".*") else pattern
    for override in overrides[strict_index + 1 :]:
        module = str(override.get("module", ""))
        if not module.startswith(prefix):
            continue
        relaxed = [name for name in _STRICT_SETTINGS if override.get(name) is False]
        assert not relaxed, (
            f"严格 scope 之后的 `{module}` 放宽了 {relaxed}——这会让范围无声缩水。"
        )
