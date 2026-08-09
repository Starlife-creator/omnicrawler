from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from e2e.harness import create_pdf_bytes, local_server, write_browser_config, write_pipeline_config
from omnicrawl.cli import main as cli_main
from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest
from omnicrawl.fetching.browser_fetcher import BrowserFetcher
from omnicrawl.pipeline import Pipeline


@pytest.mark.e2e
def test_local_crawl_pdf_extract_export_and_resume(tmp_path: Path) -> None:
    with local_server(create_pdf_bytes()) as base_url:
        config_path = write_pipeline_config(tmp_path, base_url)
        config = load_config(config_path)
        with Pipeline(config) as pipeline:
            result = pipeline.run()

        assert result["status"] == "succeeded"
        assert result["processed"] == 2
        assert result["pdf"]["result"]["extract"]["records"] == 1

        workspace = tmp_path / "workspace"
        manifest = workspace / "artifacts" / "pdf" / "source_manifest.jsonl"
        manifest_row = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
        assert manifest_row["source_url"] == f"{base_url}/notice.pdf"
        assert manifest_row["crawl_run_id"] == result["run_id"]
        for relative_path in (
            "output/pipeline_summary.json", "output/pdf/pages.jsonl", "output/pdf/text_manifest.csv",
            "output/pdf/extraction_results.xlsx", "output/pdf/review_queue.csv",
        ):
            assert (workspace / relative_path).is_file(), relative_path

        with Pipeline(config) as pipeline:
            resumed = pipeline.run()
        assert resumed["pdf"]["result"]["processing"]["ingest"]["duplicate"] == 1
        assert resumed["pdf"]["result"]["extract"]["selected"] == 0


@pytest.mark.e2e
def test_local_cli_validate_and_plan_contract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with local_server() as base_url:
        config_path = write_pipeline_config(tmp_path, base_url)
        browser_config = write_browser_config(tmp_path, base_url)
        assert "browser-e2e" in browser_config.read_text(encoding="utf-8")
        with pytest.raises(SystemExit) as validation_exit:
            cli_main(["validate", "--config", str(config_path)])
        assert validation_exit.value.code == 0
        cli_main(["plan", "--config", str(config_path)])
        with pytest.raises(HTTPError) as missing:
            urlopen(f"{base_url}/missing")
        assert missing.value.code == 404
    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert "plan" in output.casefold()


@pytest.mark.e2e
@pytest.mark.e2e_browser
def test_local_playwright_dynamic_render_xhr_capture_and_pool_reuse(tmp_path: Path) -> None:
    pytest.importorskip("playwright", reason="Install the browser E2E extra to run this extension")
    with local_server() as base_url:
        fetcher = BrowserFetcher(load_config(write_browser_config(tmp_path, base_url)))
        try:
            first = fetcher.fetch(CrawlRequest(f"{base_url}/dynamic", render=True))
            second = fetcher.fetch(CrawlRequest(f"{base_url}/dynamic", render=True))
        finally:
            fetcher.close()

    assert b"Captured E2E API value" in first.body
    captured = [item for item in first.meta["api_responses"] if item["url"].endswith("/api/items")]
    assert captured[0]["json"]["data"]["name"] == "Captured E2E API value"
    assert b"Captured E2E API value" in second.body
