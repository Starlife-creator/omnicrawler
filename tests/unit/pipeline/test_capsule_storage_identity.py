import json

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.pipeline import Pipeline
from omnicrawler.state.capsule_store import CapsuleStore


def test_capsule_identity_matches_delivered_record_after_filtering_and_deduplication(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICRAWL_CAPSULE_ENABLED", "true")
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: identity, workspace: work}\nsource: {kind: static_html, seeds: [https://example.org/]}\nextract: {mode: html, item_selector: article, deduplicate_by: [id], enrich: false, fields: {id: {selector: b}, title: {selector: h2}}}\n", encoding="utf-8")
    with Pipeline(load_config(path)) as pipeline:
        run = pipeline.state.start_run("identity", str(path))
        request = CrawlRequest("https://example.org/")
        result = FetchResult(request, request.url, 200, {"content-type": "text/html"},
                             b"<article><b>abc</b><h2>A</h2></article>", 0)
        pipeline._handle_result(run, result, 0, discover=False)
        row = pipeline.state.rows("SELECT record_id,evidence_json FROM records WHERE run_id=?", (run,))[0]
        evidence = json.loads(row["evidence_json"])
        capsules = CapsuleStore(pipeline.config.workspace / "capsules").read(run)
        assert capsules and all(c.input["record_id"] == row["record_id"] for c in capsules)
        assert evidence["_provenance"]["record_id"] == row["record_id"]
