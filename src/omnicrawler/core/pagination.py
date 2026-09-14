"""``source.pagination`` 的形状契约 —— 分页知识的**唯一真源**。

分页的形状知识此前散在三处：``core/config.py`` 只校验 ``type == "page"``；
``sources/sources.py`` 消费 ``type == "page"``（页码 / 偏移）与 ``next_path``（下一页 / 游标）；
``extraction/api_discovery.py`` 产出 ``page`` / ``next`` / ``cursor`` 三种形态。
结果是：任何一次改动都要同时改三处，而且 **GUI 无从知道"分页有哪些字段可填"**
（表单建出的 API 任务只能抓第一页，且没有任何提示）。

本模块把**形状与其字段**收成一处，供三个消费方使用：

* 配置校验（``core/config.py``）—— 借它补上了一处静默失效：游标配置若缺 ``next_path``，
  此前既不报错也不翻页，运行结果只是"少了几页"；
* GUI 表单（``gui/views/task_canvas_draft.py``）—— **由契约渲染控件**，
  因此 GUI 不再自带一份分页词典；
* 漂移守卫（``tests/unit/core/test_pagination_contract.py``）—— 断言"契约声明的形状都被
  取数引擎真正消费"、"取数引擎读的键都在契约里声明"，两个方向都机器可查。

判定形状用 :func:`detect_shape` 而**不是**直接读 ``type``：既有配置里游标形态常常没有
``type``（例如 ``{next_path: $.next, parameter: cursor}``，仓库内的基准任务与既有端到端用例
都是这么写的），只有 ``next_path`` 存在才代表"按响应里的下一页值继续"。
判定条件刻意与取数引擎的**生效条件**对齐，避免校验器对"死键"报错。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: 字段取值类型
TEXT = "text"
INTEGER = "integer"
CHOICE = "choice"


@dataclass(frozen=True)
class PaginationField:
    """分页形状里的一个字段（名字是结构约定，中文标签属表现层，不放这里）。"""

    name: str
    kind: str = TEXT
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    #: 是否应由 GUI 表单渲染；``False`` 表示契约里有、但需要配套键（如 ``source.payload``）
    #: 才能生效，仍留在透传里由高级用户/YAML 表达。
    editable: bool = True


@dataclass(frozen=True)
class PaginationShape:
    """一种分页形态：``source.pagination.type`` 的取值 + 它认得的字段。"""

    key: str
    fields: tuple[PaginationField, ...]
    aliases: tuple[str, ...] = ()


#: 页码 / 偏移式：地址栏参数逐个推进（``page=1..N``、``offset=0,50,100``）。
PAGE_SHAPE = PaginationShape(
    key="page",
    fields=(
        PaginationField("parameter", TEXT, required=True, default="page"),
        PaginationField("start", INTEGER, default=1),
        PaginationField("end", INTEGER, default=1),
        PaginationField("step", INTEGER, default=1),
        # 仅 ``location: body`` 需要 ``source.payload`` 配套，属高级用法，表单不渲染。
        PaginationField("location", CHOICE, default="query", choices=("query", "body"), editable=False),
    ),
)

#: 下一页 / 游标式：从响应里取"下一页值"。``parameter`` 为空 ⇒ 该值是一个 URL；
#: 非空 ⇒ 该值是同一个查询参数的下一个状态（必须替换旧值，而不是追加）。
CURSOR_SHAPE = PaginationShape(
    key="cursor",
    aliases=("next",),
    fields=(
        PaginationField("next_path", TEXT, required=True),
        PaginationField("parameter", TEXT),
    ),
)

#: 全部已知形状（顺序即 GUI 下拉里的顺序）。
SHAPES: tuple[PaginationShape, ...] = (PAGE_SHAPE, CURSOR_SHAPE)

#: 由契约管理的字段名集合：GUI 用它判断"哪些键归表单所有"，写回时清理另一形状的残留。
FIELD_NAMES: frozenset[str] = frozenset(field.name for shape in SHAPES for field in shape.fields)


def shape_for_type(value: object) -> PaginationShape | None:
    """按 ``type`` 取值找形状；未知取值返回 ``None``（调用方须**原样保留**，不许改写）。"""
    key = str(value or "").strip().lower()
    if not key:
        return None
    for shape in SHAPES:
        if key == shape.key or key in shape.aliases:
            return shape
    return None


def detect_shape(pagination: Mapping[str, Any] | None) -> PaginationShape | None:
    """判定一段 ``source.pagination`` 属于哪个形状。

    判定顺序与取数引擎的**生效条件**一致（不是"看到相关键就算"）：

    1. ``type`` 显式给出且已知 ⇒ 该形状（``next`` 与 ``cursor`` 是同一形状的别名）；
    2. 否则 ``next_path`` 非空 ⇒ 游标 / 下一页形状（历史配置就这么写）；
    3. 其余 ⇒ ``None``。**没有形状不等于无效** —— 比如只有 ``{parameter, start, end}``
       却没有 ``type`` 的配置，取数引擎本来就不会翻页，属"死键"，校验器不该因此判错。
    """
    if not isinstance(pagination, Mapping) or not pagination:
        return None
    explicit = shape_for_type(pagination.get("type"))
    if explicit is not None:
        return explicit
    if str(pagination.get("next_path") or "").strip():
        return CURSOR_SHAPE
    return None


def validate_pagination(pagination: Any) -> list[str]:
    """校验 ``source.pagination``，返回错误列表（空列表表示通过）。

    文案与 ``core/config.py`` 原有口径保持逐字一致（页码那几条早已被用户日志引用），
    新增的只有游标侧：``next_path`` 缺失必须**明确报错**，不能让"只采到第一页"
    以"成功"的样子交付出去。
    """
    if not pagination:
        return []
    if not isinstance(pagination, Mapping):
        return ["source.pagination必须是YAML对象"]

    shape = detect_shape(pagination)
    if shape is None:
        return []

    errors: list[str] = []
    if shape is PAGE_SHAPE:
        try:
            start = int(pagination.get("start", 1))
            end = int(pagination.get("end", start))
            if start < 0 or end < start:
                errors.append("source.pagination页码范围无效")
        except (TypeError, ValueError):
            errors.append("source.pagination.start/end必须是整数")
        if not str(pagination.get("parameter", "page")).strip():
            errors.append("source.pagination.parameter不能为空")
        if "step" in pagination:
            try:
                if int(pagination["step"]) < 1:
                    errors.append("source.pagination.step必须大于等于1")
            except (TypeError, ValueError):
                errors.append("source.pagination.step必须是整数")
        location = str(pagination.get("location") or "query").strip()
        if location not in {"query", "body"}:
            errors.append("source.pagination.location只能是query或body")
        return errors

    if not str(pagination.get("next_path") or "").strip():
        errors.append("source.pagination.next_path不能为空（游标/下一页翻页必须给出下一页字段）")
    return errors
