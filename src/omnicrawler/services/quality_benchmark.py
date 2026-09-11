"""任务质量基准：把「数据完整性与准确性」变成可复现、可比较的数字。

## 补齐什么

`services/benchmarking.py` 测的是**吞吐**（页/秒）——它回答「跑得多快」，
但不回答「采到的数据对不对、全不全、有没有来源证据」。而 `优化方案.md` §1.2 对采集能力的要求是
「**稳定获得完整、准确、有来源证据的数据**；并具备可复现的任务基准与公平对比能力」。

本模块提供那另一半：在**本地固定任务**（页面内容与期望结果都是代码里的常量）上跑真实流水线，
然后按四个口径打分：

| 口径 | 含义 |
|---|---|
| **完整性** | 期望的记录有多少条真的采到了 |
| **准确性** | 期望字段值有多少个与真实值逐字相符 |
| **来源证据** | 采到的记录里有多少条带 `source_url` |
| **字段自报完整度** | 流水线自己在 `evidence._quality.completeness` 里报的完整度均值（与外部比对**互证**） |

任务用本地 HTTP 服务提供，**全程离线**；配置由代码生成，因此「同一版本 → 同一任务 → 可比结果」。

## 为什么打分逻辑与运行分开

:func:`score_records` 是**纯函数**（记录 + 真值 → 分数），因此可以用合成数据把每一类缺陷
（缺记录 / 字段值错 / 缺来源证据）单独测出来；:func:`run_task` 只负责起服务、跑流水线、读产物。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    """一个可复现的采集任务：页面内容、抽取规则与期望结果都是常量。"""

    name: str
    pages: tuple[tuple[str, str], ...]
    item_selector: str
    fields: tuple[tuple[str, str], ...]
    expected: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class QualityScore:
    """一次任务的质量得分。``ok`` 表示四项口径是否全部满分。"""

    task: str
    expected_records: int
    found_records: int
    matched_records: int
    completeness: float
    accuracy: float
    evidence_ratio: float
    mean_field_completeness: float
    environment: tuple[tuple[str, str], ...] = ()
    config_sha256: str = ""

    @property
    def ok(self) -> bool:
        return (
            self.completeness >= 1.0
            and self.accuracy >= 1.0
            and self.evidence_ratio >= 1.0
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
) -> QualityScore:
    """把实际记录与任务真值比对，算出四项口径（**纯函数，便于单独测试**）。

    记录按 ``data`` 里的 ``title`` 与真值配对——真值里的 title 唯一，因此不会错配。
    """
    by_title: dict[str, dict[str, Any]] = {}
    for record in records:
        found_data = _as_mapping(record.get("data"))
        if str(found_data.get("title", "")):
            by_title[str(found_data["title"])] = record

    field_names = [name for name, _ in task.fields]
    matched_records = 0
    matched_fields = 0
    evidenced = 0
    quality_scores: list[float] = []

    for expected in task.expected:
        # 名字刻意与上面的 `record` 区分：那是 dict，这里是 dict | None，
        # 复用同名会让 mypy 报「赋值类型不兼容」。
        matched = by_title.get(str(expected.get("title", "")))
        if matched is None:
            continue
        matched_records += 1
        data = _as_mapping(matched.get("data"))
        for name in field_names:
            if str(data.get(name, "")) == str(expected.get(name, "")):
                matched_fields += 1
        if str(matched.get("source_url", "") or ""):
            evidenced += 1
        quality = _as_mapping(_as_mapping(matched.get("evidence")).get("_quality"))
        completeness = quality.get("completeness")
        if isinstance(completeness, int | float):
            quality_scores.append(float(completeness))

    expected_total = len(task.expected)
    denominator_fields = expected_total * max(1, len(field_names))
    return QualityScore(
        task=task.name,
        expected_records=expected_total,
        found_records=len(records),
        matched_records=matched_records,
        completeness=matched_records / expected_total if expected_total else 0.0,
        accuracy=matched_fields / denominator_fields if denominator_fields else 0.0,
        evidence_ratio=evidenced / matched_records if matched_records else 0.0,
        mean_field_completeness=(sum(quality_scores) / len(quality_scores)) if quality_scores else 0.0,
        environment=environment,
        config_sha256=config_sha256,
    )


class _Site(BaseHTTPRequestHandler):
    """把任务页面挂在本地 HTTP 上（离线、可复现）。"""

    pages: dict[str, str] = {}

    def do_GET(self):  # noqa: N802
        body = self.pages.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):  # noqa: N802
        return


def _task_config(task: BenchmarkTask, *, base_url: str, workspace: Path) -> dict[str, Any]:
    """由任务生成确定性配置——同一版本跑出同一任务，结果才可比。"""
    return {
        "project": {"name": f"qbench-{task.name}", "workspace": str(workspace)},
        # `crawl` 才会跟随站内链接；`static_html` 只抓种子页——任务因此覆盖多页抓取。
        "source": {"kind": "crawl", "seeds": [base_url]},
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
        "extract": {
            "mode": "html",
            "item_selector": task.item_selector,
            "fields": {name: {"selector": selector} for name, selector in task.fields},
        },
        "outputs": {"jsonl": True, "csv": False, "xlsx": False},
    }


def run_task(task: BenchmarkTask, *, workdir: Path) -> QualityScore:
    """在本地服务上跑一遍任务并打分（全程离线）。"""
    import yaml

    workdir.mkdir(parents=True, exist_ok=True)
    handler = type("_TaskSite", (_Site,), {"pages": dict(task.pages)})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config_data = _task_config(
            task, base_url=f"http://127.0.0.1:{server.server_port}/", workspace=workdir / "work"
        )
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
    )
