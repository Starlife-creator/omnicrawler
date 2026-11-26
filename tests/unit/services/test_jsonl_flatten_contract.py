"""`table_io._jsonl` 的展平语义 —— **固定现状**，不是背书。

## 为什么要有这个文件

交接报告里 `_jsonl._flatten_to` 的语义被标为「是缺陷还是有意，此前未定论」。
没定论时讨论它只是空谈：任何人改一行都可能悄悄改掉用户看到的数据形态，
而且**改完不会有测试变红**——这个模块此前一条直接测试都没有。

所以先做一件事：把**此刻真实的行为**逐条钉住。它不代表"这是对的"，
只代表"改动它必须是有意识的，而且会有人告诉你"。

## 已经测出来的两条真实差异

```
exporters._flatten    -> {"author.name": "张三", "author.age": 3, "tags": "[...]"}
table_io._flatten_to  -> {"author": "{\"name\": \"张三\", ...}", "tags": "[...]"}
```

同名、近义的函数，**产出不同的列名**。前者递归到底（`author.name`），
后者只下探一层就把嵌套 dict 整个 dumps 成字符串（`author`）。

而 `exporters` 里那条同名覆盖的修法（W6.4，`2ce18027`）在 `table_io` 里**没有**：
`data` 下的 `record_id` 会直接覆盖真正的 `record_id`，并且**原值彻底消失**
（`exporters` 是「改名保留」写 `data.record_id`，这里是「覆盖丢弃」）。

两条事实都有测试钉住；`test_collision_destroys_the_real_record_id` 的名字就是结论。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.pipeline.exporters import _flatten as exporter_flatten
from omnicrawler.services.table_io._jsonl import _flatten_to, read_jsonl, write_jsonl


def _record(**data: object) -> dict[str, object]:
    return {
        "record_id": "real-id",
        "source_url": "https://example.org/a",
        "record_type": "item",
        "created_at": "2026-11-26T00:00:00Z",
        "data": dict(data),
        "evidence": {"_quality": {"score": 1}},
    }


def _round_trip(tmp_path: Path, record: dict[str, object]) -> dict[str, object]:
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return read_jsonl(source, {})[0]


# ── 现状 ①：嵌套 dict 只下探一层，整体 dumps 成字符串 ──────────────────────


def test_nested_dict_is_dumped_whole_instead_of_flattened(tmp_path: Path) -> None:
    """现状：`data.author` 是一个**字符串**，不是 `author.name` / `author.age` 两列。

    这与 `exporters._flatten` 的递归展开**不同**。若哪天真要统一，这条会红 ——
    那正是提醒你「用户看到的列名要变了」的信号，不是让你直接把断言改掉。
    """
    record = _round_trip(tmp_path, _record(author={"name": "张三", "age": 3}))

    assert record["author"] == json.dumps({"name": "张三", "age": 3}, ensure_ascii=False)
    assert "author.name" not in record
    assert "author.age" not in record


def test_list_values_are_dumped_as_json_text(tmp_path: Path) -> None:
    record = _round_trip(tmp_path, _record(tags=["x", "y"]))
    assert record["tags"] == '["x", "y"]'


# ── 现状 ②：两个"展平"函数的列名并不一样 ──────────────────────────────────


def test_the_two_flatten_helpers_disagree_on_column_names() -> None:
    """同名近义的两个函数产出不同列名 —— 这就是"两份口径"的实物证据。"""
    sample = {"author": {"name": "张三"}, "tags": ["x"], "price": 9.9}

    exported: dict[str, object] = {}
    exporter_flatten("", sample, exported)
    table_io: dict[str, object] = {}
    _flatten_to("", sample, table_io)

    assert sorted(exported) == ["author.name", "price", "tags"]
    assert sorted(table_io) == ["author", "price", "tags"]
    assert sorted(exported) != sorted(table_io)


# ── 现状 ③：data 里的键覆盖基础列，且原值消失（缺陷候选） ─────────────────


def test_collision_destroys_the_real_record_id(tmp_path: Path) -> None:
    """**这条钉的是一个缺陷**，不是正确行为。

    `exporters` 为同一问题做过 W6.4 修复：冲突键写 `data.<key>`、原名保留、
    并汇总一条告警（`tests/unit/audit/test_medium_low_correct_delivery.py` 承重）。
    `table_io` 的读路径没跟上，于是：

    * 真正的 `record_id`（`real-id`）被页面数据覆盖成 `FROM-DATA`；
    * 覆盖之后**两个值都找不回来** —— 原值没了，被覆盖进去的那份也只能靠
      `data.record_id` 去找，而这里根本没写这个键。

    修它必须同时改上面两条现状中受影响的断言；所以先在这里留一条会红的线。
    """
    record = _round_trip(
        tmp_path, _record(record_id="FROM-DATA", title="Alpha")
    )

    assert record["record_id"] == "FROM-DATA", "现状：基础列被 data 覆盖"
    assert "data.record_id" not in record, "现状：没有像 exporters 那样改名保留"


def test_round_trip_through_nested_writer_loses_the_original_id(tmp_path: Path) -> None:
    """读→写的往返把原始 `record_id` 永久丢掉（不是"换个位置"，是真的没了）。"""
    original = _record(record_id="FROM-DATA", title="Alpha")
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(original, ensure_ascii=False) + "\n", encoding="utf-8")

    rows = read_jsonl(source, {})
    target = tmp_path / "back.jsonl"
    write_jsonl(rows, target, {"nested": True})
    round_tripped = json.loads(target.read_text(encoding="utf-8").strip())

    assert original["record_id"] == "real-id"
    assert round_tripped["record_id"] == "FROM-DATA", "原始值在这一步已经不在了"
    assert round_tripped["data"] == {"title": "Alpha"}, "data 里也没有留下那个被覆盖的键"


# ── 现状 ④：evidence 走 evidence_json 旁路 ────────────────────────────────


def test_evidence_is_carried_as_a_json_string_column(tmp_path: Path) -> None:
    record = _round_trip(tmp_path, _record(title="Alpha"))
    assert record["evidence_json"] == json.dumps({"_quality": {"score": 1}}, ensure_ascii=False)
    assert "evidence" not in record


def test_flat_can_be_turned_off_to_keep_the_nested_shape(tmp_path: Path) -> None:
    """`flat=False` 读回来的是原始嵌套形态 —— 这是绕过上面全部现状的开关。"""
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(_record(title="Alpha"), ensure_ascii=False) + "\n", encoding="utf-8")

    record = read_jsonl(source, {"flat": False})[0]
    assert record["record_id"] == "real-id"
    assert record["data"] == {"title": "Alpha"}
    assert record["evidence"] == {"_quality": {"score": 1}}


# ── 现状 ⑤：_flatten_to 的边界输入 ────────────────────────────────────────


def test_flatten_to_ignores_a_top_level_scalar() -> None:
    """顶层的标量没有键可用，直接丢弃（不炸、也不产出）—— 现状。"""
    out: dict[str, object] = {}
    _flatten_to("", 42, out)
    assert out == {}


def test_flatten_to_puts_a_top_level_list_under_items() -> None:
    out: dict[str, object] = {}
    _flatten_to("", [1, 2], out)
    assert out == {"items": "[1, 2]"}


def test_flatten_to_keeps_a_prefix_for_scalars() -> None:
    out: dict[str, object] = {}
    _flatten_to("p", 1, out)
    assert out == {"p": 1}


@pytest.mark.parametrize("value", [None, True, 0, ""])
def test_flatten_to_keeps_falsy_scalars_under_a_prefix(value: object) -> None:
    """假值不能被 `if prefix` 之外的某个真值判断顺手吃掉。"""
    out: dict[str, object] = {}
    _flatten_to("k", value, out)
    assert out == {"k": value}
