"""`requires_components` 字段 + `check_market()`：把「从诞生起恒为空」接上电。

## 背景（2026-11-27 实测）

`services/component_capability.py` 的 docstring 写着三件事，其中两件没实现：

1. `check_market(...)` —— **文件里根本没有这个 `def`**，只有注释提到它；
2. `resolve()` / `install_hint()` —— **零调用方**，且**物理上永远命中不了**：
   它拿 `plugin["required_capabilities"]` 里的 `ocr.*` 去和组件 `provides` 比，
   而 `required_capabilities` 的每个键**必须先过 `CAPABILITY_VERSIONS`**：

       >>> validate_required_capabilities({"ocr.paddle": ">=1"})
       ValueError: 宿主不支持插件要求的能力: ocr.paddle

   也就是说，插件的「组件需求」此前**没有任何合法字段可以表达**。

## 判据

`requires_components` 是**唯一**表达组件需求的字段：

| 字段 | 语义 | 谁校验 |
|---|---|---|
| `required_capabilities` | 宿主协议版本（现状正确，不动）| `validate_required_capabilities` → `CAPABILITY_VERSIONS` |
| `requires_components`（新）| 组件能力需求，如 `("ocr.paddle",)` | 本文件的形态校验 + `component_capability.resolve()` |

**只叫这一个名字，不再造第三个同义词。**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_contracts import PluginMetadata, _normalize_schema_fields
from omnicrawler.services.component_capability import (
    check_market,
    install_hint,
    resolve,
)


def _plugin_file(tmp_path: Path) -> Path:
    path = tmp_path / "plugin.py"
    path.write_text("def handle(o, p): return {}\n", encoding="utf-8")
    return path


# ── 字段与形态校验 ──────────────────────────────────────────────────────


def test_field_defaults_to_empty_tuple() -> None:
    assert PluginMetadata("x").requires_components == ()


def test_valid_names_are_normalised_to_a_tuple(tmp_path: Path) -> None:
    meta = _normalize_schema_fields(
        PluginMetadata("demo", requires_components=("ocr.paddle", " pdf.extract ")),  # type: ignore[arg-type]
        _plugin_file(tmp_path),
    )
    assert meta.requires_components == ("ocr.paddle", "pdf.extract")


def test_name_without_a_dot_is_rejected(tmp_path: Path) -> None:
    """★ 必须点分：`NO_DOT` 这种写法没法判断它属于哪个域。"""
    with pytest.raises(ValueError, match="不是合法的点分能力名"):
        _normalize_schema_fields(
            PluginMetadata("bad", requires_components=("NO_DOT",)), _plugin_file(tmp_path)
        )


def test_duplicate_names_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="含重复项"):
        _normalize_schema_fields(
            PluginMetadata("dup", requires_components=("ocr.a", "ocr.a")),
            _plugin_file(tmp_path),
        )


def test_string_instead_of_list_is_rejected(tmp_path: Path) -> None:
    """★ 写成裸字符串是最容易犯的错（`requires_components: ocr.paddle`）。"""
    with pytest.raises(ValueError, match="必须是列表或元组"):
        _normalize_schema_fields(
            PluginMetadata("s", requires_components="ocr.paddle"),  # type: ignore[arg-type]
            _plugin_file(tmp_path),
        )


# ── resolve 读新字段 ────────────────────────────────────────────────────


def test_resolve_reads_requires_components() -> None:
    """★ 这条是核心：新字段必须真的被消费（否则又是「定义了没有生产者」）。"""
    report = resolve(
        [{"id": "p1", "requires_components": ["ocr.paddle"]}],
        [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}],
    )
    assert report.ok is True
    assert report.checks[0].required == ["ocr.paddle"]
    assert report.checks[0].satisfied_by == {"ocr.paddle": ["ocr-paddle"]}


def test_resolve_reports_missing_when_no_component_provides_it() -> None:
    report = resolve([{"id": "p1", "requires_components": ["ocr.paddle"]}], [])
    assert report.ok is False
    assert report.checks[0].missing == ["ocr.paddle"]


def test_resolve_ignores_host_protocol_capabilities() -> None:
    """宿主核心能力（`records.page`）不该被当成"缺组件"。"""
    report = resolve(
        [{"id": "p1", "required_capabilities": {"records.page": ">=1"},
          "requires_components": ["ocr.paddle"]}],
        [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}],
    )
    assert report.checks[0].required == ["ocr.paddle"]


def test_resolve_deduplicates_across_both_fields() -> None:
    """同一能力写在两个字段里，只该算一次。"""
    report = resolve(
        [{"id": "p1", "requires_components": ["ocr.paddle"],
          "required_capabilities": {"ocr.paddle": ">=1"}}],
        [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}],
    )
    assert report.checks[0].required == ["ocr.paddle"]


# ── check_market ────────────────────────────────────────────────────────


def test_check_market_passes_when_every_need_is_covered() -> None:
    assert check_market(
        [{"id": "p1", "requires_components": ["ocr.paddle"]}],
        [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}],
    ) == []


def test_check_market_flags_uncovered_need() -> None:
    problems = check_market(
        [{"id": "p1", "requires_components": ["ocr.paddle"]}], []
    )
    assert len(problems) == 1
    assert "p1" in problems[0] and "ocr.paddle" in problems[0]


def test_check_market_rejects_empty_plugin_list() -> None:
    """★ 空集合判红：否则「没有插件」与「所有插件都 OK」给出同样的绿。"""
    problems = check_market([], [{"id": "c", "provides": ["ocr.a"]}])
    assert problems and "空集合不是通过" in problems[0]


# ── install_hint ────────────────────────────────────────────────────────


def test_install_hint_names_the_component_to_install() -> None:
    """★ 缺组件时给「装 X 解锁」，而不是让用户撞 `ModuleNotFoundError`。"""
    report = resolve(
        [{"id": "p1", "requires_components": ["ocr.paddle"]}],
        [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}],
    )
    assert install_hint(report.checks[0], [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}]) == ""


def test_install_hint_is_honest_when_no_component_exists() -> None:
    """没有对应组件时如实说「暂无」，不编一个。"""
    report = resolve([{"id": "p1", "requires_components": ["ocr.paddle"]}], [])
    hint = install_hint(report.checks[0], [])
    assert "暂无组件" in hint, hint
