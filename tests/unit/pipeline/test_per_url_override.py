"""B-2 闸门：per-URL 模板强制覆盖在 Runner 提取阶段生效（P2-5a）。

覆盖核心契约：
- source.seed_template_overrides（GUI 写入的 per_url_template_overrides 序列化键）
- 命中 URL 时：覆盖模板的 extract 段与基础 extract 段 deep_merge（模板优先）
- 未命中 / 模板缺失 / 空值：优雅回退默认提取，不抛异常
- 共享 self.config 不被修改（多线程抓取安全）
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.pipeline import Pipeline
from omnicrawler.pipeline._extract import _strip_placeholders

_HTML = b"""<html><head><title>Page</title></head><body>
<article><p>article text</p></article>
<table><tr><td>Cell A</td><td>Cell B</td></tr></table>
</body></html>"""


def _base_config(tmp_path: Path, *, seed: str, overrides: dict[str, str]) -> Path:
    config_path = tmp_path / "project.yaml"
    config_path.write_text(yaml.safe_dump({
        "project": {"name": "per_url", "workspace": str(tmp_path / "work")},
        "source": {
            "kind": "static_html",
            "seeds": [seed],
            "seed_template_overrides": overrides,
        },
        "http": {"respect_robots": False, "allow_private_network": True, "delay_seconds": 0},
        "extract": {"mode": "html", "item_selector": "article", "fields": {"cell": {"selector": "td"}}},
    }, sort_keys=False), encoding="utf-8")
    return config_path


def _result(seed: str, *, final_url: str | None = None) -> FetchResult:
    request = CrawlRequest(url=seed)
    return FetchResult(
        request=request,
        final_url=final_url or seed,
        status=200,
        headers={"content-type": "text/html"},
        body=_HTML,
        elapsed_seconds=0.01,
    )


def test_override_hit_merges_template_extract(tmp_path: Path) -> None:
    seed = "https://example.com/list"
    config = load_config(_base_config(tmp_path, seed=seed, overrides={seed: "generic/html-table"}))
    with Pipeline(config) as pipeline:
        merged, temp_config = pipeline._per_url_extract_override(_result(seed))
        assert merged is not None
        assert temp_config is not None
        # 模板 item_selector=table 覆盖基础 article
        assert merged["item_selector"] == "table"
        # 模板字段为空，基础 fields 保留（deep_merge 语义）
        assert merged["fields"] == {"cell": {"selector": "td"}}
        # 模板引入的 review_low_confidence 生效
        assert merged["review_low_confidence"] is True
        # 临时配置仅本条文档生效，共享 self.config 未被修改（线程安全）
        assert temp_config.section("extract")["item_selector"] == "table"
        assert pipeline.config.section("extract")["item_selector"] == "article"


def test_no_override_returns_none(tmp_path: Path) -> None:
    seed = "https://example.com/list"
    config = load_config(_base_config(tmp_path, seed=seed, overrides={}))
    with Pipeline(config) as pipeline:
        merged, temp_config = pipeline._per_url_extract_override(_result(seed))
        assert merged is None and temp_config is None


def test_override_absent_for_other_url_returns_none(tmp_path: Path) -> None:
    seed = "https://example.com/list"
    config = load_config(_base_config(tmp_path, seed=seed, overrides={seed: "generic/html-table"}))
    with Pipeline(config) as pipeline:
        merged, _ = pipeline._per_url_extract_override(_result("https://other.example/detail"))
        assert merged is None


def test_unknown_template_falls_back_gracefully(tmp_path: Path) -> None:
    seed = "https://example.com/list"
    config = load_config(_base_config(tmp_path, seed=seed, overrides={seed: "no/such-template"}))
    with Pipeline(config) as pipeline:
        merged, temp_config = pipeline._per_url_extract_override(_result(seed))
        assert merged is None and temp_config is None


def test_empty_override_value_ignored(tmp_path: Path) -> None:
    seed = "https://example.com/list"
    config = load_config(_base_config(tmp_path, seed=seed, overrides={seed: ""}))
    with Pipeline(config) as pipeline:
        merged, temp_config = pipeline._per_url_extract_override(_result(seed))
        assert merged is None and temp_config is None


def test_final_url_fallback_match(tmp_path: Path) -> None:
    seed = "https://example.com/list"
    final = "https://example.com/redirected"
    config = load_config(_base_config(tmp_path, seed=seed, overrides={final: "generic/html-table"}))
    with Pipeline(config) as pipeline:
        merged, _ = pipeline._per_url_extract_override(_result(seed, final_url=final))
        assert merged is not None
        assert merged["item_selector"] == "table"


def test_strip_placeholders_recursive() -> None:
    assert _strip_placeholders("{{list_selector}}") == ""
    assert _strip_placeholders("article {{x}} h2") == "article h2"
    assert _strip_placeholders({"a": "{{x}}", "b": ["{{y}}", "keep"]}) == {"a": "", "b": ["", "keep"]}
    assert _strip_placeholders(123) == 123


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_HTML)))
        self.end_headers()
        self.wfile.write(_HTML)

    def log_message(self, format, *args):  # noqa: N802
        return


def _run_once(tmp_path: Path, server: ThreadingHTTPServer, *, with_override: bool) -> list[dict]:
    url = f"http://127.0.0.1:{server.server_port}/"
    overrides = {url: "generic/html-table"} if with_override else {}
    config = load_config(_base_config(tmp_path, seed=url, overrides=overrides))
    with Pipeline(config) as pipeline:
        summary = pipeline.run()
        rows = pipeline.state.rows(
            "SELECT data_json FROM records WHERE run_id=?", (summary["run_id"],)
        )
    return [json.loads(row["data_json"]) for row in rows]


def test_end_to_end_override_changes_extraction(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with_override = _run_once(tmp_path, server, with_override=True)
        without = _run_once(tmp_path, server, with_override=False)
        # 覆盖命中：item_selector=table → 表格内 td 可提取到 cell 字段
        assert any("cell" in record.get("data", record) for record in with_override)
        # 未覆盖：item_selector=article → article 内无 td，cell 字段缺失
        assert not any("cell" in record.get("data", record) for record in without)
    finally:
        server.shutdown()
        server.server_close()
