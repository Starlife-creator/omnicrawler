from __future__ import annotations

import json

from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.services.metrics import RunMetrics


def test_metrics_write_json_and_prometheus(tmp_path) -> None:
    metrics = RunMetrics()
    request = CrawlRequest("https://example.org/page")
    result = FetchResult(request, request.url, 200, {"content-type": "text/html"}, b"hello", 0.25)
    metrics.record_fetch(result, engine="http")
    for seconds in (0.1, 0.2, 0.3, 0.4, 0.5):
        metrics.record_stage("crawl", seconds)
    metrics.increment("omnicrawl_failures_total", stage="parse", error='bad"value')
    metrics.gauge("omnicrawl_frontier_pending", 3)

    paths = metrics.write(tmp_path / "output", tmp_path)
    snapshot = json.loads((tmp_path / "output" / "metrics.json").read_text(encoding="utf-8"))
    prometheus = (tmp_path / "output" / "metrics.prom").read_text(encoding="utf-8")

    assert paths["json"].endswith("metrics.json")
    assert snapshot["latency_by_host"]["example.org"]["count"] == 1
    assert snapshot["stage_durations"]["crawl"]["p50_seconds"] == 0.3
    assert snapshot["stage_durations"]["crawl"]["p95_seconds"] == 0.48
    assert 'status="200"' in prometheus
    assert 'omnicrawl_request_duration_seconds_max{host="example.org"} 0.250000' in prometheus
    assert "omnicrawl_frontier_pending 3.0" in prometheus
    assert 'omnicrawl_stage_duration_seconds_p95{stage="crawl"} 0.480000' in prometheus
    # B13-003：标签值含双引号必须被转义（CWE-116 信息类），否则 .prom 输出不可解析
    assert 'error="bad\\"value"' in prometheus, prometheus
    assert 'bad"value' not in prometheus.replace('bad\\"value', ""), (
        "标签值中的裸双引号必须全部转义"
    )
