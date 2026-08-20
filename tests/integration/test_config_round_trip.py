"""S3.3.2：配置往返 e2e + 结构化证据路径参数化测试。"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from omnicrawler.core.config import load_config as load_core_config


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html><title>Round Trip</title><h1>Item A</h1></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: N802
        return


def test_gui_config_round_trip_runs_and_produces_records(tmp_path: Path) -> None:
    """GUI 配置序列化 → 核心 load_config → CLI 语义 run → 产出记录。"""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])

    from omnicrawler.gui.core.config_model import CrawlConfig
    from omnicrawler.gui.core.config_serializer import save_yaml
    from omnicrawler.pipeline import Pipeline

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = CrawlConfig(
            project_name="roundtrip",
            workspace=str(tmp_path / "work"),
            seed_urls=[f"http://127.0.0.1:{server.server_port}/"],
        )
        config.max_pages = 1
        yaml_path = tmp_path / "task.yaml"
        save_yaml(config, yaml_path)

        core_config = load_core_config(yaml_path)
        core_config.raw["http"]["allow_private_network"] = True
        core_config.raw["http"]["respect_robots"] = False
        core_config.raw["http"]["delay_seconds"] = 0
        with Pipeline(core_config) as pipeline:
            summary = pipeline.run()
        assert summary["status"] == "succeeded"
        assert summary["records"] >= 1
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "typo",
    [
        {"projext": {"name": "x"}},
        {"source": {"kind": "crawl", "seedz": ["https://example.com/"]}},
        {"crawel": {"max_pages": 5}},
        {"extract": {"mode": "html", "fiels": {"title": {"selector": "title"}}}},
        {"http": {"timeout_secondz": 25}},
        {"outputs": {"exporter": "default", "jsnl": True}},
        {"download": {"enabl": True}},
        {"session": {"persist_cookiez": True}},
        {"processors": {"pdf": {"enabeld": True}}},
        {"updates": {"enbled": True}},
    ],
)
def test_ten_typos_are_all_rejected(typo: dict) -> None:
    from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge, validate_config

    raw = deep_merge(
        DEFAULTS,
        {
            "project": {"name": "t", "workspace": "work"},
            "source": {"kind": "crawl", "seeds": ["https://example.com/"]},
            **typo,
        },
    )
    config = AppConfig(Path("<memory>"), Path.cwd(), raw, Path.cwd())
    errors, warnings = validate_config(config, strict=True)
    assert errors, f"拼写错误未被拦截: {typo}"
    assert any("未知" in item or "无效" in item or "未知键" in item for item in errors + warnings)


@pytest.mark.parametrize(
    "style", ["card", "table", "list"],
)
def test_export_single_record_styles_with_structured_evidence(
    tmp_path: Path, style: str,
) -> None:
    from omnicrawler.export.markdown_exporter import MarkdownExporter

    record = {
        "record_id": "rec-1",
        "source_url": "https://example.org/1",
        "field_values": [
            {"field_name": "金额", "value": "1200000", "confidence": 0.95,
             "evidence": {"raw": "Revenue: 1,200,000", "page": 1}},
            {"field_name": "日期", "value": "2024-03-01"},
        ],
    }
    target = tmp_path / f"record_{style}.md"
    md = MarkdownExporter.export_single_record(record, target, style=style)
    assert target.is_file()
    assert "rec-1" in md
    assert "金额" in md
    assert "1200000" in md
    # 结构化证据（dict）：card 样式输出证据原文；table/list 只输出字段值
    if style == "card":
        assert "Revenue: 1,200,000" in md
    else:
        assert "2024-03-01" in md
