"""Extract mixin: result handling (extract/discover/archive) and quality staging."""

from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

from ..core.models import FetchResult
from ..extraction import extractors
from ..extraction.api_discovery import write_discovery_bundle
from ..extraction.topic_filter import evaluate_topic, filter_records
from ..plugins.plugin_runtime import transform_record
from ..quality.data_intelligence import enrich_records
from ..quality.quality import assess_records
from ._mixin_base import _PipelineBase

LOGGER = logging.getLogger("omnicrawl")


class _PipelineExtract(_PipelineBase):
    def _handle_result(
        self,
        run_id: str,
        result: FetchResult,
        maximum_depth: int,
        *,
        persist_response: bool = True,
        discover: bool = True,
    ) -> None:
        raw_path: Path | None = None
        archive = (
            persist_response
            and result.status != 304
            and bool(self.config.section("incremental").get("archive_raw", True))
        )
        if archive and result.request.kind != "asset":
            suffix = mimetypes.guess_extension(result.content_type) or ".bin"
            stored = self.object_store.put(
                f"raw/{result.request.fingerprint}_{result.content_hash[:12]}{suffix}",
                result.body,
                content_type=result.content_type,
            )
            raw_path = stored.local_path
        api_responses = result.meta.get("api_responses", []) if persist_response else []
        if api_responses:
            api_archive = self.object_store.put(
                f"raw/browser_api/{result.request.fingerprint}_{result.content_hash[:12]}.json",
                json.dumps(api_responses, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                content_type="application/json",
            )
            result.meta["api_archive_path"] = (
                str(api_archive.local_path) if api_archive.local_path else api_archive.uri
            )
            if self.config.section("browser").get("auto_generate_api_templates", True):
                discovery = write_discovery_bundle(
                    api_responses,
                    self.workspace / "output" / "api_discovery",
                    name=result.request.fingerprint[:16],
                )
                result.meta["api_discovery"] = discovery
                self._api_discoveries.append(discovery)
        changed = (
            self.state.save_response(run_id, result, str(raw_path) if raw_path else None)
            if persist_response
            else True
        )

        is_binary = extractors.choose_processor(result) == "binary"
        if result.request.kind == "asset" or is_binary:
            topic_config = self.config.section("selection").get("topic", {})
            if isinstance(topic_config, dict) and topic_config.get("enabled", False):
                candidate = {
                    "url": result.final_url,
                    "anchor": result.request.meta.get("anchor", ""),
                    "title": result.request.meta.get("title", ""),
                }
                decision = evaluate_topic(candidate, topic_config)
                keep_candidate = bool(topic_config.get("keep_uncertain", True)) and not decision.excluded
                if not decision.matched and not keep_candidate:
                    LOGGER.info("附件因主题规则被跳过: %s (%s)", result.final_url, decision.reason)
                    return
            path = self._save_artifact(result)
            self.state.save_artifact(run_id, result, path)
            return

        if not changed and self.config.section("incremental").get("skip_unchanged", True):
            return

        # === Stage: Extract ===
        mode = str(self.config.section("extract").get("mode", "auto")).lower()
        processor_name = extractors.choose_processor(result) if mode == "auto" else mode
        parser_name = str(self.config.section("extract").get("parser", "")).strip().casefold()
        extractor_name = str(self.config.section("extract").get("extractor", "")).strip().casefold()
        if parser_name and extractor_name:
            raise ValueError("extract.parser and extract.extractor cannot both be selected")
        if parser_name or extractor_name or processor_name in self.registry.processors:
            processor = (
                self._processor(parser_name, parser=True)
                if parser_name
                else self._processor(extractor_name, extractor=True)
                if extractor_name
                else self._processor(processor_name)
            )
            outcome = processor.process(result)
            for transformer in self._transformers:
                outcome.records = [transform_record(transformer, record) for record in outcome.records]
            topic_config = self.config.section("selection").get("topic", {})
            if (
                isinstance(topic_config, dict)
                and topic_config.get("enabled", False)
                and topic_config.get("filter_records", True)
            ):
                outcome.records = filter_records(outcome.records, topic_config)
            extract_config = self.config.section("extract")
            fields = extract_config.get("fields", {})
            intelligence = enrich_records(outcome.records, self.config)
            self.metrics.increment(
                "omnicrawl_entities_resolved_total", intelligence["entities_resolved"]
            )
            self.metrics.increment(
                "omnicrawl_near_duplicate_records_total", intelligence["near_duplicates"]
            )
            if isinstance(fields, dict) and fields:
                self._stage_quality(run_id, outcome.records, fields, extract_config)
            semantic_changes = self.state.track_semantic_changes(run_id, outcome.records)
            self.metrics.increment("omnicrawl_semantic_changes_total", len(semantic_changes))
            observation = self.template_monitor.observe(
                result,
                outcome.records,
                fields if isinstance(fields, dict) else {},
            )
            if observation and observation.invalidated:
                LOGGER.warning("Template invalidated for %s: %s", result.final_url, observation.suggestions)
            self.state.save_records(run_id, result.request, outcome.records)
            if persist_response:
                self.regression_library.capture(
                    result, records=len(outcome.records), processor=processor_name
                )
            self.record_sinks.write(run_id, result.request, outcome.records)
            self.metrics.increment("omnicrawl_records_total", len(outcome.records), processor=processor_name)
            self._emit(
                "after_extract",
                run_id=run_id,
                result=result,
                records=outcome.records,
                count=len(outcome.records),
                processor=parser_name or extractor_name or processor_name,
            )

        # === Stage: Discover ===
        if not discover or result.request.depth >= maximum_depth:
            return
        for child in self.source.discover(result):
            topic_config = self.config.section("selection").get("topic", {})
            strict_prefilter = isinstance(topic_config, dict) and bool(
                topic_config.get("strict_link_prefilter", False)
            )
            if (
                self.config.source_kind == "focused"
                and strict_prefilter
                and child.kind == "page"
                and child.priority <= 0
            ):
                continue
            root = child.meta.get("root_url")
            allowed, _reason = self.scope.allowed(child.url, str(root) if root else None)
            if allowed:
                self.state.enqueue(child)

    def _stage_quality(
        self,
        run_id: str,
        records: list[Any],
        fields: dict[str, Any],
        extract_config: dict[str, Any],
    ) -> None:
        """Run quality assessment on extracted records and record metrics."""
        quality_summary = assess_records(
            records,
            fields,
            float(extract_config.get("quality_threshold", 0.8)),
            [str(item) for item in extract_config.get("unique_by", [])],
        )
        self.metrics.increment("omnicrawl_review_required_total", quality_summary["review_required"])
        self.metrics.increment("omnicrawl_duplicate_records_total", quality_summary["duplicates"])
        self.metrics.increment("omnicrawl_anomalies_total", quality_summary["anomalies"])
        self.state.add_quality_stats(run_id, quality_summary["field_stats"])
        for field_name, values in quality_summary["field_stats"].items():
            self.metrics.increment(
                "omnicrawl_field_present_total", values["present"], field=field_name
            )
            self.metrics.increment(
                "omnicrawl_field_missing_total",
                values["total"] - values["present"],
                field=field_name,
            )
            self.metrics.increment(
                "omnicrawl_field_invalid_total",
                values["present"] - values["valid"],
                field=field_name,
            )
