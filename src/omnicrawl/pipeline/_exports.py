"""Export mixin: run exporters, assemble run summary and persist it."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from ..core.utils import atomic_write
from ..plugins.plugin_runtime import run_exporter
from ..runtime.resource_profiles import profile_for
from ._mixin_base import _PipelineBase


class _PipelineExports(_PipelineBase):
    def _run_exports(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        outputs = self.config.section("outputs")
        primary = str(outputs.get("exporter", "default")).strip().casefold() or "default"
        extras_raw = outputs.get("plugin_exporters", [])
        if not isinstance(extras_raw, list):
            raise TypeError("outputs.plugin_exporters must be a list")
        names = [primary, *(str(item).strip().casefold() for item in extras_raw)]
        names = list(dict.fromkeys(name for name in names if name))
        options_by_name = outputs.get("exporter_options", {})
        if not isinstance(options_by_name, dict):
            raise TypeError("outputs.exporter_options must be a mapping")

        self._emit("before_export", run_id=run_id, exporters=names)
        results: dict[str, Any] = {}
        for name in names:
            if name not in self.registry.exporters:
                raise KeyError(f"Unknown exporter plugin: {name}")
            options = options_by_name.get(name, {})
            if not isinstance(options, dict):
                raise TypeError(f"Exporter options must be a mapping: {name}")
            idempotency_key = f"{run_id}:export:{name}"
            if not self.state.begin_export(run_id, name, idempotency_key):
                commit = self.state.export_commit(idempotency_key)
                if not force and commit and commit["status"] == "succeeded":
                    results[name] = commit["result"]
                    continue
                # S2.5.2：force（reprocess）路径绕过幂等提交缓存，重新导出刷新输出文件
                if force:
                    if commit is None:
                        raise RuntimeError(f"导出器{name}提交状态异常，拒绝重复提交")
                    self.state.begin_export(run_id, name, idempotency_key, force=True)
                else:
                    raise RuntimeError(f"导出器{name}已有未完成提交，拒绝重复提交")
            try:
                value = run_exporter(
                    self.registry.exporters[name], self.config, self.state, run_id, options
                )
                result_value = value if isinstance(value, dict) else {"value": value}
                self.state.finish_export(idempotency_key, result_value)
                results[name] = result_value
            except Exception as exc:
                self.state.fail_export(idempotency_key, str(exc))
                if name == primary or not self.config.section("plugins").get("fail_open", False):
                    raise
                self.registry.plugin_errors.append({
                    "path": f"exporter:{name}", "error": f"{type(exc).__name__}: {exc}"
                })
        self._emit("after_export", run_id=run_id, results=results)
        primary_result = results.get(primary, {})
        if not isinstance(primary_result, dict):
            primary_result = {"result": primary_result}
        else:
            primary_result = dict(primary_result)
        primary_result["plugin_exporters"] = {
            name: value for name, value in results.items() if name != primary
        }
        return primary_result

    def _stage_exports(
        self,
        run_id: str,
        status: str,
        processed: int,
        pdf_summary: dict[str, Any] | None,
        callback: Callable[[str, dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        """Run exporters, assemble summary, emit lifecycle hooks and persist."""
        export_started = time.monotonic()
        exported = self._run_exports(run_id)
        self.metrics.record_stage("export", time.monotonic() - export_started)
        summary: dict[str, Any] = {
            "run_id": run_id, "status": status, "processed": processed,
            **self.state.stats(run_id), "export": exported, "pdf": pdf_summary,
        }
        summary["api_discovery"] = {
            "bundles": len(self._api_discoveries),
            "endpoints": sum(int(item.get("endpoints", 0)) for item in self._api_discoveries),
            "items": self._api_discoveries,
        }
        summary["template_health"] = self.template_monitor.summary()
        summary["resource_profile"] = profile_for(self.config).to_dict()
        summary["metrics"] = self.metrics.write(self.workspace / "output", self.workspace)
        summary["plugins"] = self.registry.describe()
        summary["storage"] = self.record_sinks.status()
        summary["storage_warnings"] = summary["storage"]["recent_errors"]
        summary["egress_audit"] = self.egress.audit_status()
        self._emit("after_run", run_id=run_id, summary=summary)
        self._write_pipeline_summary(summary)
        self.state.finish_run(run_id, status, summary)
        if callback:
            callback("completed", summary)
        return summary

    def _write_pipeline_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        summary_path = self.workspace / "output" / "pipeline_summary.json"
        summary["pipeline_summary"] = str(summary_path)
        atomic_write(
            summary_path,
            json.dumps(summary, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
        )
        return summary
