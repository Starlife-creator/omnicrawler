"""能力表 ⇄ 代码 必须一致（`check_capability_docs` 门禁）。

## 它守什么

`docs/PLUGIN_CONTRACT.md` 的能力表是**插件作者唯一能看到的清单**。
它一旦落后于代码，作者会按错的清单写插件 —— 而这类漂移**不报错、不红测试**：

```
代码有、文档无（修前 17 条）: artifact.stream.*, resources.*, view.progress,
                            surface.background.*, component.ocr.recognize, …
```

## 判据（四个方向）

| 方向 | 判据 |
|---|---|
| 代码 → 文档 | `CAPABILITY_VERSIONS` 的每条都得在文档里有交代 |
| 文档 → 代码 | **能力表**里点名的必须真实存在于代码里 |
| 声明式能力 | 显式登记在 `_DECLARATION_ONLY`，且代码里**真的没有**处理器 |
| 空集合 | `CAPABILITY_VERSIONS` 为空 ⇒ 判红 |
"""

from __future__ import annotations

import pytest

from tools.gates.check_capability_docs import (
    _DECLARATION_ONLY,
    capability_table_rows,
    claimed_capabilities,
    documented_capabilities,
    handler_capabilities,
    main,
    mislabelled_declaration_only,
    phantom,
    undocumented,
)

# ── 文档解析 ────────────────────────────────────────────────────────────


def test_slash_shorthand_expands_to_full_names() -> None:
    """`state.set/delete/migrate` 必须展开成三个完整能力名。"""
    text = "\n".join([
        "| 能力 | 所需权限 | 说明 |",
        "|---|---|---|",
        "| `state.set/delete/migrate` | `state:write` | x |",
    ])
    assert claimed_capabilities(text) == {"state.set", "state.delete", "state.migrate"}


def test_only_the_capability_table_counts_as_a_promise() -> None:
    """★ 只有能力表算「宿主承诺支持」。

    文档里还有一张「`source.seed` 载荷字段」表，点名 `source.file` /
    `source.files` / `source.input_file` —— 那是**载荷字段名**，不是宿主能力。
    把它们当成能力会让门禁误报（我第一版就是这样，5 处假警报）。
    """
    text = "\n".join([
        "| 字段 | 类型 | 说明 |",
        "|---|---|---|",
        "| `source.file` | str | 载荷字段，不是能力 |",
        "",
        "| 能力 | 所需权限 | 说明 |",
        "|---|---|---|",
        "| `records.page` | `records:read` | 真能力 |",
    ])
    assert capability_table_rows(text) == [
        "|---|---|---|",
        "| `records.page` | `records:read` | 真能力 |",
    ]
    assert claimed_capabilities(text) == {"records.page"}


def test_payload_field_names_are_not_capabilities() -> None:
    """★ 单段名字（`auth` / `response_ref` / `theme_id`）不是能力 —— 必须点分。

    实测：第一版正则把 `.` 写成了未转义的 `.`（匹配任意字符），于是
    `auth` 这种单段名也被当成「能力」，报出 3 处假警报。
    """
    text = "\n".join([
        "| 能力 | 所需权限 | 说明 |",
        "|---|---|---|",
        "| `network.fetch` | `network:scoped` | 凭据经 `auth` 注入 |",
        "| `theme.apply` | `theme:apply` | 只接受 `theme_id` |",
    ])
    assert claimed_capabilities(text) == {"network.fetch", "theme.apply"}


def test_missing_table_yields_nothing_rather_than_everything() -> None:
    """★ 找不到能力表时返回空 —— 让「空集合判红」那条判据接住。"""
    assert capability_table_rows("# 没有表格") == []
    assert claimed_capabilities("# 没有表格") == set()


# ── 三个方向 ────────────────────────────────────────────────────────────


def test_undocumented_finds_a_missing_capability() -> None:
    assert undocumented({"a.b", "c.d"}, {"a.b"}, set()) == ["c.d"]


def test_declaration_only_capabilities_are_not_required_in_the_table() -> None:
    """★ `view.richtext` 是声明式能力：它不该被要求进能力表。"""
    assert undocumented({"view.richtext"}, set(), {"view.richtext"}) == []


def test_phantom_finds_a_doc_claim_without_code() -> None:
    assert phantom({"ghost.capability"}, {"records.page"}) == ["ghost.capability"]


def test_mislabelled_declaration_only_is_reported() -> None:
    """★ 登记成「仅声明」但**其实有处理器** ⇒ 漏登记，比文档漂移更严重。"""
    assert mislabelled_declaration_only({"view.richtext"}, {"view.richtext"}) == [
        "view.richtext"
    ]


def test_handler_names_are_recovered_from_source() -> None:
    """`_cap_surface_background_set` ⇒ `surface.background.set`。"""
    source = "    def _cap_surface_background_set(self, payload): ...\n"
    assert handler_capabilities(source) == {"surface.background.set"}


# ── 真实仓库 ────────────────────────────────────────────────────────────


def test_real_repository_is_consistent() -> None:
    """端到端：真实仓库必须通过。"""
    assert main([]) == 0


def test_declaration_only_registry_is_not_empty_in_name_only() -> None:
    """★ `_DECLARATION_ONLY` 里每一条都必须真的在 `CAPABILITY_VERSIONS` 里。

    过期登记会让「仅声明 ⇒ 无处理器」那条判据空转。
    """
    from omnicrawler.plugins.plugin_broker_contracts import CAPABILITY_VERSIONS

    assert set(_DECLARATION_ONLY) <= set(CAPABILITY_VERSIONS)


def test_documented_superset_of_claimed() -> None:
    """全文口径的 `documented` 必须是能力表口径的 `claimed` 的超集。"""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / "docs" / "PLUGIN_CONTRACT.md").read_text(
        encoding="utf-8"
    )
    assert claimed_capabilities(text) <= documented_capabilities(text)


@pytest.mark.parametrize("empty", [set(), frozenset()])
def test_empty_capability_set_is_red_for_undocumented(empty) -> None:
    """空集合不算通过 —— 但这里验的是调用侧语义（`main` 里有独立判红）。"""
    assert undocumented(set(), set(), set()) == []
    assert undocumented(empty, set(), set()) == []
