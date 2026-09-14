"""`source.pagination` 契约的护栏：形状、校验文案，以及**两个方向的机器检查**。

## 为什么要单独给"分页契约"写测试

分页的形状知识原本散在三处（`core/config.py` 只认 `type=page`、`sources/sources.py` 消费
`type=page` 与 `next_path`、`extraction/api_discovery.py` 产出 `page`/`next`/`cursor`），
GUI 因此无从知道"分页有哪些字段可填"。契约收拢到 `core/pagination.py` 之后，
**这两件事必须由测试保证**，否则契约会慢慢变成一份没人对得上的文档：

1. **契约 → 引擎**：每个声明的形状都真的被取数引擎消费（不是装饰）；
2. **引擎 → 契约**：引擎读的每个键都在契约里声明（新增形状时漏登记会被判红）。

第 2 条用**源码扫描**实现 —— 它能发现"引擎读了契约不知道的键"，而这正是形状知识再次
分叉的入口。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config, validate_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.core.pagination import (
    CURSOR_SHAPE,
    FIELD_NAMES,
    PAGE_SHAPE,
    SHAPES,
    detect_shape,
    shape_for_type,
    validate_pagination,
)
from omnicrawler.sources.sources import GenericSource

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 引擎侧读取分页配置的文件（契约必须覆盖它们读的全部键）。
_ENGINE_FILES = (
    _REPO_ROOT / "src" / "omnicrawler" / "sources" / "sources.py",
    _REPO_ROOT / "src" / "omnicrawler" / "pipeline" / "_run.py",
)

#: 契约之外的合法键：`type` 是形状标识本身，不属于任何形状的字段表。
_NON_FIELD_KEYS = {"type"}


def _config(tmp_path: Path, source: dict) -> object:
    value = {
        "project": {"name": "pagination-contract", "workspace": str(tmp_path / "workspace")},
        "source": {"kind": "rest", "seeds": ["https://example.org/start"], **source},
        "http": {"resolve_dns": False, "respect_robots": False},
    }
    path = tmp_path / "pagination.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return load_config(path)


# ── 契约本身 ─────────────────────────────────────────────────────────────


def test_declared_shapes_cover_page_and_cursor() -> None:
    assert {shape.key for shape in SHAPES} == {"page", "cursor"}
    # `next` 与 `cursor` 是同一形状的两种写法（引擎对二者的处理完全相同）。
    assert shape_for_type("next") is CURSOR_SHAPE
    assert shape_for_type("cursor") is CURSOR_SHAPE
    assert shape_for_type("page") is PAGE_SHAPE
    # 未知取值必须返回 None：插件自带的形状不能被核心改写。
    assert shape_for_type("scroll") is None
    assert shape_for_type(None) is None


def test_field_names_are_the_union_of_shapes() -> None:
    expected = {field.name for shape in SHAPES for field in shape.fields}
    assert FIELD_NAMES == expected
    assert {"parameter", "start", "end", "step", "next_path"} <= FIELD_NAMES


def test_detect_shape_mirrors_engine_activation_conditions() -> None:
    """判定条件必须与"引擎什么时候真的会翻页"一致，否则校验器会对死键报错。"""
    # 历史写法：游标形态常常没有 type（仓库内的基准任务与既有端到端用例都这么写）
    assert detect_shape({"next_path": "$.next", "parameter": "cursor"}) is CURSOR_SHAPE
    assert detect_shape({"type": "next", "next_path": "$.next"}) is CURSOR_SHAPE
    assert detect_shape({"type": "page", "parameter": "page"}) is PAGE_SHAPE
    # 没有 type、也没有 next_path ⇒ 引擎不会翻页（死键）⇒ 契约也不认形状
    assert detect_shape({"parameter": "page", "start": 1, "end": 3}) is None
    assert detect_shape({}) is None
    assert detect_shape(None) is None


# ── 校验：老文案逐字保留 + 补上游标侧的静默失效 ──────────────────────────


@pytest.mark.parametrize(
    ("pagination", "message"),
    [
        ({"type": "page", "parameter": "", "start": 1, "end": 3}, "source.pagination.parameter不能为空"),
        ({"type": "page", "parameter": "p", "start": 5, "end": 1}, "source.pagination页码范围无效"),
        ({"type": "page", "parameter": "p", "start": "x", "end": 1}, "source.pagination.start/end必须是整数"),
        (["not", "a", "mapping"], "source.pagination必须是YAML对象"),
    ],
)
def test_page_shape_keeps_the_original_error_wording(pagination: object, message: str) -> None:
    """页码侧文案与行为**保持原样**（这些字符串早已出现在用户可见的错误里）。"""
    assert message in validate_pagination(pagination)


def test_cursor_without_next_path_is_now_rejected() -> None:
    """游标配置缺 `next_path` 此前**既不报错也不翻页** —— 结果只是"少了几页"。

    这是本契约补上的第一处静默失效：运行会成功、产物看起来正常，但只有第一批结果。
    """
    issues = validate_pagination({"type": "cursor", "parameter": "cursor"})
    assert issues and "next_path" in issues[0], issues
    # 反向：合法的游标配置（含历史无 type 写法）不得被误判
    assert validate_pagination({"type": "cursor", "next_path": "$.next", "parameter": "cursor"}) == []
    assert validate_pagination({"next_path": "$.next", "parameter": "cursor"}) == []
    # 死键仍然不报错（引擎本来就不翻页）
    assert validate_pagination({"parameter": "page", "start": 1, "end": 3}) == []


def test_step_and_location_are_validated() -> None:
    assert validate_pagination({"type": "page", "parameter": "p", "step": 0}) == [
        "source.pagination.step必须大于等于1"
    ]
    assert validate_pagination({"type": "page", "parameter": "p", "step": "x"}) == [
        "source.pagination.step必须是整数"
    ]
    assert validate_pagination({"type": "page", "parameter": "p", "location": "cookie"}) == [
        "source.pagination.location只能是query或body"
    ]
    assert validate_pagination({"type": "page", "parameter": "p", "location": "body"}) == []


def test_core_validation_actually_goes_through_the_contract(tmp_path: Path) -> None:
    """核心校验必须**调用**契约（否则契约只是摆设）。

    走真实入口 `load_config`：不合法的游标配置要在**加载时**就被拒绝，而不是等跑到一半
    才发现只采了第一页。
    """
    from omnicrawler.core.errors import ConfigParseError

    with pytest.raises(ConfigParseError) as excinfo:
        _config(tmp_path, {"pagination": {"type": "cursor", "parameter": "cursor"}})
    assert "next_path" in str(excinfo.value), excinfo.value

    healthy = _config(tmp_path, {"pagination": {"next_path": "$.next", "parameter": "cursor"}})
    errors, _warnings = validate_config(healthy)
    assert not [error for error in errors if "pagination" in error], errors


# ── 机器检查 ①：契约 → 引擎（声明的形状真的会翻页）───────────────────────


def test_every_declared_shape_is_consumed_by_the_engine(tmp_path: Path) -> None:
    """每个声明形状都要在**引擎**上验一次，不能只是契约里的一行字。"""
    page_source = GenericSource(
        _config(
            tmp_path,
            {"pagination": {"type": "page", "parameter": "page", "start": 2, "end": 6, "step": 2}},
        )
    )
    assert [item.meta["page"] for item in page_source.seed()] == [2, 4, 6]

    cursor_source = GenericSource(
        _config(tmp_path, {"pagination": {"next_path": "$.next", "parameter": "cursor"}})
    )
    result = FetchResult(
        CrawlRequest("https://example.org/items", meta={"root_url": "https://example.org/items"}),
        "https://example.org/items",
        200,
        {"content-type": "application/json"},
        json.dumps({"items": [], "next": "page-2"}).encode(),
        0.1,
    )
    follow = cursor_source._discover_api_next(result)
    assert len(follow) == 1, follow
    assert follow[0].url == "https://example.org/items?cursor=page-2"


# ── 机器检查 ②：引擎 → 契约（引擎读的键都在契约里）──────────────────────


def test_engine_read_keys_are_all_declared_in_the_contract() -> None:
    """扫描引擎源码里读分页配置的键，断言它们**全部**在契约里声明过。

    这条是防止形状知识再次分叉的关键：谁在引擎里读了新键（比如新加一种分页形态），
    就必须同时更新契约 —— 否则 GUI 编不出来、校验器也看不见它。
    """
    pattern = re.compile(r"(?:pagination|source_pagination)\s*(?:\.get\(\s*|\[]\s*)['\"]([A-Za-z_]+)['\"]")
    found: dict[str, Path] = {}
    for path in _ENGINE_FILES:
        for key in pattern.findall(path.read_text(encoding="utf-8")):
            found.setdefault(key, path)

    # 先确认扫描真的扫到了东西（否则"全部都在契约里"是假通过）
    assert found, f"未在 {[path.name for path in _ENGINE_FILES]} 中扫到任何分页键，检查正则是否失效"
    undeclared = {key: path.name for key, path in found.items() if key not in FIELD_NAMES | _NON_FIELD_KEYS}
    assert not undeclared, f"引擎读了契约未声明的分页键：{undeclared}"
