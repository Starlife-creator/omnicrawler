"""场景基因增强的**可观测性**回归：配了 `extract.scene` 就必须留下读数。

问题：`pipeline/_extract.py` 此前把 `gene_augment_html(...)` 的返回值**直接丢弃**。
后果不是"少了个日志"，而是：配了 `extract.scene` 的运行，跑完之后从日志、指标、
产物里**都读不到**补提了几条、命中/落空多少。基因池是"在已有基因里择优"的闭环，
没有读数就既无法验收，也无法拿它做对照实验。

本文件用**真实 Pipeline + 本地静态站点**（离线）驱动，只断言指标层：

* 配了 `extract.scene` ⇒ 5 个基因增强指标必须出现；
* **不**配 `extract.scene` ⇒ 这些指标必须**一个都不出现**（默认关闭 ⇒ 零行为、
  零开销；这条同时证明门控是真的，而不是指标恒发）。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
from omnicrawler.pipeline import Pipeline
from omnicrawler.state.scene_store import SceneStore

_PAGE = (
    "<html><head><title>年报</title></head><body>"
    "<h1>星云科技 2026 年年度报告</h1>"
    "<p>公司名称：星云科技</p>"
    "<p>营业收入 12.5 亿元</p>"
    "<p>归属于上市公司股东的净利润 3.1 亿元</p>"
    "<p>2026 年 3 月 1 日</p>"
    "</body></html>"
).encode()

#: `annual_report` 场景的 4 个槽位，选择器故意指向**不存在的类名** ⇒ 抽取阶段取不到值
#: ⇒ 全部落进 `missing` ⇒ 基因补提必然被触发。
#:
#: ★ 两个前置条件缺一不可（都是实测踩出来的，不是猜的）：
#:   ① 光配 `extract.scene` 不够——`gene_augment_html` 闸 1 要求 `fields` 非空；
#:   ② 光让场景槽位全不匹配也不行——模板会在**第一次**抓取就被判「关键字段成功率下降」
#:      而失效、回退通用抽取、**产出 0 条记录**，闸 1 的 `not records` 又提前返回
#:      （`docs/BENCHMARKING.md` 记录过同一个坑）。所以另配一个**能匹配**的必填字段
#:      `title` 把记录与模板撑住，场景槽位一律 `required: false`。
_SCENE_FIELDS: dict[str, dict[str, object]] = {
    "title": {"selectors": ["h1"], "required": True},
    "company": {"selectors": [".absent-company"], "required": False},
    "revenue": {"selectors": [".absent-revenue"], "required": False},
    "net_profit": {"selectors": [".absent-net-profit"], "required": False},
    "report_date": {"selectors": [".absent-report-date"], "required": False},
}

_GENE_COUNTERS = (
    "omnicrawler_gene_augment_fields_total",
    "omnicrawler_gene_augment_hits_total",
    "omnicrawler_gene_augment_misses_total",
    "omnicrawler_gene_augment_skipped_no_gene_total",
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 命名
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture(scope="module")
def local_site() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


def _config(tmp_path: Path, url: str, *, scene: str) -> AppConfig:
    raw = deep_merge(dict(DEFAULTS), {"http": {"allow_private_network": True}})
    raw.setdefault("project", {"name": "gene-obs", "workspace": str(tmp_path / "work")})
    raw["source"] = {"kind": "static_html", "seeds": [url]}
    raw["http"]["respect_robots"] = False
    raw["http"]["delay_seconds"] = 0
    raw["extract"] = {"mode": "html", "item_selector": "body", "fields": dict(_SCENE_FIELDS)}
    if scene:
        raw["extract"]["scene"] = scene
    return AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")


def _counters(pipeline: Pipeline) -> dict[str, int]:
    snapshot = pipeline.metrics.snapshot()
    return {item["name"]: int(item["value"]) for item in snapshot["counters"]}


def _gauges(pipeline: Pipeline) -> dict[str, float]:
    return dict(pipeline.metrics.snapshot()["gauges"])


def test_gene_augment_reports_metrics_when_scene_configured(
    tmp_path: Path, local_site: str
) -> None:
    """配了 `extract.scene` ⇒ 基因增强指标必须出现在运行指标里。"""
    config = _config(tmp_path, local_site, scene="annual_report")
    # 场景库要先存在，否则 gene_augment 的闸 2（scene.sqlite3 不存在）会提前返回
    SceneStore(config.workspace / "scene.sqlite3").import_bundled_scenes()

    with Pipeline(config) as pipeline:
        summary = pipeline.run()
        counters = _counters(pipeline)
        gauges = _gauges(pipeline)

    assert summary["status"] in {"succeeded", "partial_success"}, summary
    for name in _GENE_COUNTERS:
        assert name in counters, f"缺少基因增强指标 {name}；现有：{sorted(counters)}"
    assert gauges.get("omnicrawler_gene_augment_active") == 1.0, gauges

    # 循环必须**真的跑过**，否则上面四个恒为 0 的计数器毫无鉴别力。
    # ★ 这里刻意不断言 hit/miss 的具体数值：随包发布的 `annual_report` 场景 4 个基因
    #   全是 `selector_type: regex`，而本仓 `_extract_with_selector` 只认 xpath、
    #   其余走 `document.cssselect()` ⇒ 这些基因当前必然落空（miss）。
    #   修掉它是另一件事；此处只断言"补提闭环被触发并留下了读数"。
    attempted = (
        counters["omnicrawler_gene_augment_hits_total"]
        + counters["omnicrawler_gene_augment_misses_total"]
        + counters["omnicrawler_gene_augment_skipped_no_gene_total"]
    )
    assert attempted >= 1, f"补提循环未触发（计数器全 0）：{counters}"


def test_gene_augment_metrics_absent_without_scene(tmp_path: Path, local_site: str) -> None:
    """★ 反向断言：不配 `extract.scene` ⇒ 这些指标必须**一个都不出现**。

    默认关闭的语义是"零行为、零开销"。若这条不成立（指标恒发），
    上面的正向断言就没有鉴别力——它证明不了指标真的来自基因增强。
    """
    config = _config(tmp_path, local_site, scene="")

    with Pipeline(config) as pipeline:
        summary = pipeline.run()
        counters = _counters(pipeline)
        gauges = _gauges(pipeline)

    assert summary["status"] in {"succeeded", "partial_success"}, summary
    leaked = [name for name in _GENE_COUNTERS if name in counters]
    assert leaked == [], f"未配 scene 却出现了基因增强指标：{leaked}"
    assert "omnicrawler_gene_augment_active" not in gauges
