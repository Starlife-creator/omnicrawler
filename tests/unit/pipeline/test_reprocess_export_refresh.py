"""S2.5.2：reprocess 强制刷新导出（绕过幂等提交缓存）。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline


def test_reprocess_export_force_refreshes_outputs(tmp_path: Path) -> None:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s252, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "outputs: {exporter: boom, jsonl: false, csv: false, xlsx: false}\n",
        encoding="utf-8",
    )
    calls: list[int] = []

    def _exporter(_config, _state, run_id, _options) -> dict:
        calls.append(1)
        return {"records": len(calls)}

    with Pipeline(load_config(config_path)) as pipeline:
        pipeline.registry.register_exporter("boom", _exporter)
        run_id = pipeline.state.start_run("s252", str(config_path))
        first = pipeline._run_exports(run_id)
        assert first["records"] == 1
        cached = pipeline._run_exports(run_id)
        assert cached["records"] == 1
        assert calls == [1]
        refreshed = pipeline._run_exports(run_id, force=True)
        assert refreshed["records"] == 2
        assert calls == [1, 1]
        pipeline.state.finish_run(run_id, "succeeded", {"status": "succeeded"})
