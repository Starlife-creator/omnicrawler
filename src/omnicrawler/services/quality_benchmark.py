"""任务质量基准：把「数据完整性与准确性」变成可复现、可比较的数字。

## 补齐什么

`services/benchmarking.py` 测的是**吞吐**（页/秒）——它回答「跑得多快」，
但不回答「采到的数据对不对、全不全、有没有来源证据」。而 `优化方案.md` §1.2 对采集能力的要求是
「**稳定获得完整、准确、有来源证据的数据**；并具备可复现的任务基准与公平对比能力」。

本模块提供那另一半：在**本地固定任务**（页面内容与期望结果都是代码里的常量）上跑真实流水线，
然后按交付契约打分：

| 口径 | 含义 |
|---|---|
| **完整性** | 期望的记录有多少条真的采到了 |
| **准确性** | 期望字段值有多少个与真实值逐字相符 |
| **来源证据** | 采到的记录里有多少条带 `source_url` |
| **字段自报完整度** | 流水线自己在 `evidence._quality.completeness` 里报的完整度均值（与外部比对**互证**） |
| **额外与重复交付** | 真值之外的记录、同一业务身份被重复交付的记录 |

任务用本地 HTTP 服务提供，**全程离线**；配置由代码生成，因此「同一版本 → 同一任务 → 可比结果」。

## 为什么打分逻辑与运行分开

:func:`score_records` 是**纯函数**（记录 + 真值 → 分数），因此可以用合成数据把每一类缺陷
（缺记录 / 字段值错 / 缺来源证据）单独测出来；:func:`run_task` 只负责起服务、跑流水线、读产物。
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.config import load_config
from ..pipeline import Pipeline
from .benchmarking import _environment, _sha256_file

#: 默认任务：种子页是列表（本身也含匹配块）+ 三条明细页。
#:
#: 种子页**必须**含匹配块：否则模板会在第一次抓取时被判「关键字段成功率下降」而失效，
#: 整轮运行的配置抽取随之退化为通用抽取——实测会得到 0 条记录。
_LIST_HTML = (
    "<html><body>"
    + "".join(
        f'<div class="item"><h1 class="t">列表{index}</h1>'
        f'<span class="p">{index * 11}</span>'
        f'<a href="/detail-{index}">明细{index}</a></div>'
        for index in (1, 2, 3)
    )
    + "</body></html>"
)


def _detail_html(title: str, price: str) -> str:
    return (
        "<html><body>"
        f'<div class="item"><h1 class="t">{escape(title)}</h1>'
        f'<span class="p">{escape(price)}</span></div>'
        "</body></html>"
    )
def _paged_list_html(page: int) -> str:
    """分页列表的一页：每页 2 条，标题跨页唯一（真值因此可判"翻页后总数正确"）。"""
    first = (page - 1) * 2 + 1
    return "<html><body>" + "".join(
        f'<div class="item"><h1 class="t">列表{index}</h1>'
        f'<span class="p">{index * 11}</span></div>'
        for index in (first, first + 1)
    ) + "</body></html>"


def _api_body(item_id: str, value: str, next_cursor: str | None) -> str:
    """REST 一页的响应体：`items` + `next`（游标，null 表示末页）。"""
    return json.dumps(
        {"items": [{"id": item_id, "value": value}], "next": next_cursor},
        ensure_ascii=False,
    )


#: 卡片内字段分散：`<a>` 只包图片与标题，**价格在 `<a>` 之外**（woocommerce 常见形态）。
_CARD_HTML = (
    "<html><body><ul class=\"products\">"
    + "".join(
        f'<li class="product"><a href="/p/{index}"><img src="/i{index}.jpg">'
        f'<h2 class="title">卡片{index}</h2></a>'
        f'<span class="price">{index}9</span></li>'
        for index in (1, 2, 3)
    )
    + "</ul></body></html>"
)


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    """一个可复现的采集任务：页面内容、抽取规则与期望结果都是常量。"""

    name: str
    pages: tuple[tuple[str, str], ...]
    item_selector: str
    fields: tuple[tuple[str, str], ...]
    expected: tuple[dict[str, str], ...]
    identity_fields: tuple[str, ...] = ("title",)
    expected_source_paths: tuple[str, ...] = ()
    expected_source_origin: str = ""

    # ---- 形态声明（默认值 = 原 HTML/爬取形态 ⇒ 既有任务零行为变化）----
    #: `crawl`（跟随站内链接）/ `static_html`（只抓种子页）/ `rest`（API）
    source_kind: str = "crawl"
    #: `html` / `json`；决定 `fields` 的第二项是 CSS 选择器还是 JSONPath
    extract_mode: str = "html"
    #: json 模式的记录路径（如 `$.items[*]`）
    item_path: str = ""
    #: 本地站点响应的 Content-Type（json 任务需要 `application/json`）
    content_type: str = "text/html; charset=utf-8"
    #: **原样透传**给 `source.pagination`（如 `(("type","page"),("parameter","page"),("start","1"),("end","2"))`）
    #: —— 不在这里重新编码键名，避免出现第二份分页词典。
    pagination: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class QualityScore:
    """一次任务的质量得分。``ok`` 表示交付结果满足精确任务契约。"""

    task: str
    expected_records: int
    found_records: int
    matched_records: int
    completeness: float
    accuracy: float
    evidence_ratio: float
    mean_field_completeness: float
    unexpected_records: int = 0
    duplicate_records: int = 0
    reported_completeness_violations: int = 0
    environment: tuple[tuple[str, str], ...] = ()
    config_sha256: str = ""

    @property
    def ok(self) -> bool:
        return (
            self.completeness >= 1.0
            and self.accuracy >= 1.0
            and self.evidence_ratio >= 1.0
            and self.unexpected_records == 0
            and self.duplicate_records == 0
            and self.mean_field_completeness >= 1.0
            and self.reported_completeness_violations == 0
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "expected_records": self.expected_records,
            "found_records": self.found_records,
            "matched_records": self.matched_records,
            "completeness": round(self.completeness, 4),
            "accuracy": round(self.accuracy, 4),
            "evidence_ratio": round(self.evidence_ratio, 4),
            "mean_field_completeness": round(self.mean_field_completeness, 4),
            "unexpected_records": self.unexpected_records,
            "duplicate_records": self.duplicate_records,
            "reported_completeness_violations": self.reported_completeness_violations,
            "ok": self.ok,
            "environment": dict(self.environment),
            "config_sha256": self.config_sha256,
        }


#: 内置任务集合。新增任务即扩展基准覆盖面（页面结构、字段数、页数都可不同）。
TASKS: tuple[BenchmarkTask, ...] = (
    BenchmarkTask(
        name="list-three-details",
        pages=(
            ("/", _LIST_HTML),
            ("/detail-1", _detail_html("明细一", "10")),
            ("/detail-2", _detail_html("明细二", "20")),
            ("/detail-3", _detail_html("明细三", "30")),
        ),
        item_selector="div.item",
        fields=(("title", "h1.t"), ("price", "span.p")),
        expected=(
            {"title": "列表1", "price": "11"},
            {"title": "列表2", "price": "22"},
            {"title": "列表3", "price": "33"},
            {"title": "明细一", "price": "10"},
            {"title": "明细二", "price": "20"},
            {"title": "明细三", "price": "30"},
        ),
        identity_fields=("title",),
        expected_source_paths=("/", "/", "/", "/detail-1", "/detail-2", "/detail-3"),
    ),
    # ① 分页列表：`source.pagination`（type=page + start/end）—— 覆盖"翻页后总数正确"。
    #    既有任务靠"站内链接发现"多页，分页**配置**这条通路此前没有任何任务覆盖。
    BenchmarkTask(
        name="list-two-pages",
        pages=(
            ("/", _paged_list_html(1)),
            ("/?page=1", _paged_list_html(1)),
            ("/?page=2", _paged_list_html(2)),
        ),
        item_selector="div.item",
        fields=(("title", "h1.t"), ("price", "span.p")),
        expected=(
            {"title": "列表1", "price": "11"},
            {"title": "列表2", "price": "22"},
            {"title": "列表3", "price": "33"},
            {"title": "列表4", "price": "44"},
        ),
        identity_fields=("title",),
        expected_source_paths=("/", "/", "/", "/"),
        pagination=(
            ("type", "page"),
            ("parameter", "page"),
            ("start", "1"),
            ("end", "2"),
            ("step", "1"),
        ),
    ),
    # ② REST 游标：`source.pagination`（next_path + parameter）+ `extract.mode=json`。
    #    API 取数通道此前在基准里完全没有代表。
    BenchmarkTask(
        name="api-cursor-two-pages",
        pages=(
            ("/", _api_body("1", "first", "p2")),
            ("/?cursor=p2", _api_body("2", "second", None)),
        ),
        item_selector="",
        fields=(("id", "id"), ("value", "value")),
        expected=(
            {"id": "1", "value": "first"},
            {"id": "2", "value": "second"},
        ),
        identity_fields=("id",),
        expected_source_paths=("/", "/"),
        source_kind="rest",
        extract_mode="json",
        item_path="$.items[*]",
        content_type="application/json",
        pagination=(("next_path", "$.next"), ("parameter", "cursor")),
    ),
    # ③ 卡片内字段分散（价格在 `<a>` 之外）+ `static_html`：覆盖"单页、不跟随链接"的形态，
    #    以及 woocommerce 那种"链接只包标题、价格在旁边"的卡片结构。
    BenchmarkTask(
        name="card-price-outside-link",
        pages=(("/", _CARD_HTML),),
        item_selector="li.product",
        fields=(("title", "a > h2.title"), ("price", "span.price")),
        expected=(
            {"title": "卡片1", "price": "19"},
            {"title": "卡片2", "price": "29"},
            {"title": "卡片3", "price": "39"},
        ),
        identity_fields=("title",),
        expected_source_paths=("/", "/", "/"),
        source_kind="static_html",
    ),
)


def _as_mapping(value: object) -> dict[str, Any]:
    """把任意来源的映射值收成 ``dict[str, Any]``；非映射一律返回空字典。

    记录来自 JSONL，字段类型在静态上只能是 ``Any``；显式收窄比到处写
    ``isinstance`` 更清楚，也让 mypy 不必猜。
    """
    return dict(value) if isinstance(value, Mapping) else {}


def score_records(
    task: BenchmarkTask,
    records: list[dict[str, Any]],
    *,
    environment: tuple[tuple[str, str], ...] = (),
    config_sha256: str = "",
    source_origin: str = "",
) -> QualityScore:
    """把实际记录与任务真值比对（**纯函数，便于用反例验证**）。

    身份字段由任务声明；同一身份的实际记录保留为列表，不再被字典静默覆盖。每条真值
    消费最接近的一条候选，未消费记录作为额外交付，其中身份已出现的另计为重复。
    """
    if not task.identity_fields:
        raise ValueError(f"{task.name}: identity_fields 不能为空")
    if task.expected_source_paths and len(task.expected_source_paths) != len(task.expected):
        raise ValueError(f"{task.name}: expected_source_paths 必须与 expected 等长")

    def identity(data: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(str(data.get(name, "")) for name in task.identity_fields)

    by_identity: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_identity[identity(_as_mapping(record.get("data")))].append(record)

    field_names = [name for name, _ in task.fields]
    matched_records = 0
    matched_fields = 0
    evidenced = 0
    quality_scores: list[float] = []
    reported_completeness_violations = 0
    expected_identities = {identity(expected) for expected in task.expected}

    for index, expected in enumerate(task.expected):
        candidates = by_identity.get(identity(expected), [])
        if not candidates:
            continue
        matched = max(
            candidates,
            key=lambda candidate: sum(
                str(_as_mapping(candidate.get("data")).get(name, ""))
                == str(expected.get(name, ""))
                for name, _ in task.fields
            ),
        )
        candidates.remove(matched)
        matched_records += 1
        data = _as_mapping(matched.get("data"))
        for name in field_names:
            if str(data.get(name, "")) == str(expected.get(name, "")):
                matched_fields += 1
        source_url = str(matched.get("source_url", "") or "")
        expected_source = task.expected_source_paths[index] if task.expected_source_paths else ""
        parsed_source = urlsplit(source_url)
        expected_origin = urlsplit(source_origin or task.expected_source_origin)
        source_matches_origin = not expected_origin.netloc or (
            parsed_source.scheme == expected_origin.scheme
            and parsed_source.netloc == expected_origin.netloc
        )
        if (
            source_url
            and source_matches_origin
            and (not expected_source or parsed_source.path == expected_source)
        ):
            evidenced += 1
        quality = _as_mapping(_as_mapping(matched.get("evidence")).get("_quality"))
        reported = quality.get("completeness")
        present_fields = sum(bool(str(data.get(name, "") or "")) for name, _ in task.fields)
        actual_field_completeness = present_fields / max(1, len(task.fields))
        if isinstance(reported, int | float):
            reported_value = float(reported)
            quality_scores.append(reported_value)
            if abs(reported_value - actual_field_completeness) > 1e-9:
                reported_completeness_violations += 1
        else:
            reported_completeness_violations += 1

    expected_total = len(task.expected)
    denominator_fields = expected_total * max(1, len(field_names))
    leftovers = [record for candidates in by_identity.values() for record in candidates]
    duplicate_records = sum(
        identity(_as_mapping(record.get("data"))) in expected_identities for record in leftovers
    )
    return QualityScore(
        task=task.name,
        expected_records=expected_total,
        found_records=len(records),
        matched_records=matched_records,
        completeness=matched_records / expected_total if expected_total else 0.0,
        accuracy=matched_fields / denominator_fields if denominator_fields else 0.0,
        evidence_ratio=evidenced / matched_records if matched_records else 0.0,
        mean_field_completeness=(sum(quality_scores) / len(quality_scores)) if quality_scores else 0.0,
        unexpected_records=len(leftovers),
        duplicate_records=duplicate_records,
        reported_completeness_violations=reported_completeness_violations,
        environment=environment,
        config_sha256=config_sha256,
    )


class _Site(BaseHTTPRequestHandler):
    """把任务页面挂在本地 HTTP 上（离线、可复现）。"""
    #: 由 `run_task` 按任务声明覆盖（json 任务需 `application/json`）
    content_type: str = "text/html; charset=utf-8"

    pages: dict[str, str] = {}

    def do_GET(self):  # noqa: N802
        body = self.pages.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):  # noqa: N802
        return


def _task_config(task: BenchmarkTask, *, base_url: str, workspace: Path) -> dict[str, Any]:
    """由任务生成确定性配置——同一版本跑出同一任务，结果才可比。

    形态由任务声明：HTML 默认 `crawl`（跟随站内链接，覆盖多页抓取）；
    `rest` 走 API 通道；分页**原样透传** `task.pagination` 给 `source.pagination`
    （不在这里重新编码键名，避免出现第二份分页词典）。
    """
    source: dict[str, Any] = {"kind": task.source_kind, "seeds": [base_url]}
    if task.pagination:
        source["pagination"] = dict(task.pagination)

    extract: dict[str, Any] = {"mode": task.extract_mode}
    if task.extract_mode == "json":
        extract["item_path"] = task.item_path
        extract["fields"] = {name: {"path": path} for name, path in task.fields}
    else:
        extract["item_selector"] = task.item_selector
        extract["fields"] = {name: {"selector": selector} for name, selector in task.fields}

    return {
        "project": {"name": f"qbench-{task.name}", "workspace": str(workspace)},
        "source": source,
        "crawl": {
            "max_pages": len(task.pages) + 1,
            "max_depth": 2,
            "concurrency": 2,
            "same_host": True,
            "allow_domains": ["127.0.0.1"],
        },
        "http": {
            "user_agent": "omnicrawler-benchmark@example.org",
            "respect_robots": False,
            "delay_seconds": 0,
            "allow_private_network": True,
            "retries": 0,
        },
        "extract": extract,
        "outputs": {"jsonl": True, "csv": False, "xlsx": False},
    }


def run_task(task: BenchmarkTask, *, workdir: Path) -> QualityScore:
    """在本地服务上跑一遍任务并打分（全程离线）。"""
    import yaml

    workdir.mkdir(parents=True, exist_ok=True)
    handler = type("_TaskSite", (_Site,), {"pages": dict(task.pages), "content_type": task.content_type})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/"
        config_data = _task_config(task, base_url=base_url, workspace=workdir / "work")
        config_path = workdir / "task.yaml"
        config_path.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")
        with Pipeline(load_config(config_path)) as pipeline:
            pipeline.run()
    finally:
        server.shutdown()
        server.server_close()

    records_path = workdir / "work" / "output" / "records.jsonl"
    records: list[dict[str, Any]] = []
    if records_path.is_file():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return score_records(
        task,
        records,
        environment=_environment(),
        config_sha256=_sha256_file(config_path),
        source_origin=base_url,
    )
