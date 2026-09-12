"""固定游标 API 的真实 HTTP 流水线验收。"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline


class _CursorApi(BaseHTTPRequestHandler):
    """刻意只读取首个 cursor 值，复现常见服务端分页语义。"""

    pages = {
        "": ({"id": 1, "value": "first"}, "page-2"),
        "page-2": ({"id": 2, "value": "middle"}, "page-3"),
        "page-3": ({"id": 3, "value": "last"}, None),
    }

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        cursor = urllib.parse.parse_qs(parsed.query).get("cursor", [""])[0]
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        item, next_cursor = self.server.pages[cursor]  # type: ignore[attr-defined]
        body = json.dumps({"items": [item], "next": next_cursor}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # noqa: N802
        return


@pytest.fixture
def cursor_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CursorApi)
    server.hits = []  # type: ignore[attr-defined]
    server.pages = dict(_CursorApi.pages)  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _write_config(tmp_path: Path, seed: str) -> Path:
    config = {
        "project": {"name": "cursor-api", "workspace": str(tmp_path / "work")},
        "source": {
            "kind": "rest",
            "seeds": [seed],
            "pagination": {"next_path": "$.next", "parameter": "cursor"},
        },
        "crawl": {
            "max_pages": 10,
            "max_depth": 5,
            "same_host": True,
            "allow_domains": ["127.0.0.1"],
            "concurrency": 1,
        },
        "http": {
            "user_agent": "CursorPipelineTest/1.0 (+contact: test@example.org)",
            "allow_private_network": True,
            "respect_robots": False,
            "delay_seconds": 0,
            "retries": 0,
        },
        "extract": {
            "mode": "json",
            "item_path": "$.items[*]",
            "fields": {"id": {"path": "id"}, "value": {"path": "value"}},
        },
        "outputs": {"jsonl": True, "csv": False, "xlsx": False},
    }
    path = tmp_path / "cursor-api.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def test_cursor_api_reaches_last_page_once_and_stops(cursor_api, tmp_path: Path) -> None:
    seed = f"http://127.0.0.1:{cursor_api.server_port}/items?scope=all"
    config = load_config(_write_config(tmp_path, seed))

    with Pipeline(config) as pipeline:
        summary = pipeline.run()

    assert summary["status"] == "succeeded", summary
    assert summary["processed"] == 3
    assert cursor_api.hits == [
        "/items?scope=all",
        "/items?scope=all&cursor=page-2",
        "/items?scope=all&cursor=page-3",
    ]
    assert all(hit.count("cursor=") <= 1 for hit in cursor_api.hits)

    records_path = config.workspace / "output" / "records.jsonl"
    records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]
    assert [record["data"] for record in records] == [
        {"id": 1, "value": "first"},
        {"id": 2, "value": "middle"},
        {"id": 3, "value": "last"},
    ]
    assert [record["source_url"] for record in records] == [
        seed,
        f"{seed}&cursor=page-2",
        f"{seed}&cursor=page-3",
    ]

    # 新同步必须重建整条游标链；未变化页只记响应，不重复交付，末页变化只交付一次。
    cursor_api.hits.clear()
    cursor_api.pages["page-3"] = ({"id": 3, "value": "last-updated"}, None)
    with Pipeline(config) as pipeline:
        second = pipeline.run()

    assert second["status"] == "succeeded", second
    assert second["processed"] == 3
    assert second["records"] == 1
    assert cursor_api.hits == [
        "/items?scope=all",
        "/items?scope=all&cursor=page-2",
        "/items?scope=all&cursor=page-3",
    ]
    changed = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["data"] for record in changed] == [
        {"id": 3, "value": "last-updated"},
    ]
