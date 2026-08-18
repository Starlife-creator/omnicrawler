"""Extract mixin: result handling (extract/discover/archive) and quality staging."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from ..core.errors import ExtractionError
from ..core.models import FetchResult
from ..extraction import extractors
from ..extraction.api_discovery import write_discovery_bundle
from ..extraction.topic_filter import evaluate_topic, filter_records
from ..plugins.plugin_runtime import transform_record
from ..quality.data_intelligence import enrich_records
from ..quality.quality import assess_records
from ._mixin_base import _PipelineBase

LOGGER = logging.getLogger("omnicrawler")

# B-2 闸门：模板渲染后可能残留的未填充占位符（如 {{list_selector}}）
_PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}")


def _strip_placeholders(value: Any) -> Any:
    """递归清空模板渲染后残留的 {{...}} 占位符，避免字面文本泄漏进选择器。

    占位符清空后把连续空白折叠为单个空格，避免选择器中出现无效的双空格分隔。
    """
    if isinstance(value, dict):
        return {key: _strip_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_placeholders(item) for item in value]
    if isinstance(value, str):
        cleaned = _PLACEHOLDER_RE.sub("", value).strip()
        return re.sub(r"\s+", " ", cleaned)
    return value


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

        # S4.5 P3#136：choose_processor 只调一次（binary 判定与提取选择共用）
        processor_name = extractors.choose_processor(result)
        is_binary = processor_name == "binary"
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
        # B-2 闸门：逐 URL 模板强制覆盖（source.seed_template_overrides）。
        # 命中时仅本条文档使用覆盖后的提取配置（临时 AppConfig，不改共享 self.config，线程安全）。
        per_url_extract, per_url_config = self._per_url_extract_override(result)
        extract_sec = per_url_extract if per_url_extract is not None else self.config.section("extract")
        mode = str(extract_sec.get("mode", "auto")).lower()
        # S4.5 P3#136：复用上方 binary 判定的 processor_name（auto 模式）
        if mode != "auto":
            processor_name = mode
        parser_name = str(extract_sec.get("parser", "")).strip().casefold()
        extractor_name = str(extract_sec.get("extractor", "")).strip().casefold()
        if parser_name and extractor_name:
            raise ValueError("extract.parser and extract.extractor cannot both be selected")
        if parser_name or extractor_name or processor_name in self.registry.processors:
            try:
                # S2.5.33：提取阶段整体隔离——异常以 ExtractionError 上抛，
                # 上游记为 stage="extract" 而非 "fetch"
                processor = (
                    self._processor(parser_name, parser=True, config=per_url_config)
                    if parser_name
                    else self._processor(extractor_name, extractor=True, config=per_url_config)
                    if extractor_name
                    else self._processor(processor_name, config=per_url_config)
                )
                outcome = processor.process(result)
                for transformer in self._transformers:
                    outcome.records = [transform_record(transformer, record) for record in outcome.records]
                # B-1 证据胶囊：提取后、归一化前（门控 OMNICRAWL_CAPSULE_ENABLED=true）
                self._capture_capsules(run_id, result, outcome.records, extract_sec)
                # N1：场景基因增强（默认关闭：无 extract.scene 时零行为）
                # 在归一化前补提缺失字段，让增强值经过 _normalize_records 收敛类型
                scene_name = str(extract_sec.get("scene", "") or "")
                if scene_name:
                    try:
                        from ..quality.gene_augment import gene_augment_html

                        fields_map = extract_sec.get("fields", {})
                        gene_augment_html(
                            result,
                            outcome.records,
                            fields_map if isinstance(fields_map, dict) else {},
                            scene_name,
                            Path(self.config.workspace) / "scene.sqlite3",
                        )
                    except Exception:  # noqa: BLE001 — 基因增强失败绝不阻断提取
                        LOGGER.warning("基因增强失败", exc_info=True)
                # AutoDataCleaner 值清洗：L1 幂等 + L2 规则（quality.normalize，默认开），
                # 在主题筛选前执行，让 enrich/质量评估/导出消费已清洗的值
                self._normalize_records(outcome.records)
                topic_config = self.config.section("selection").get("topic", {})
                if (
                    isinstance(topic_config, dict)
                    and topic_config.get("enabled", False)
                    and topic_config.get("filter_records", True)
                ):
                    outcome.records = filter_records(outcome.records, topic_config)
                # B-2：质量评估使用覆盖后的提取段（fields/quality_threshold/unique_by 同步生效）
                extract_config = extract_sec
                fields = extract_config.get("fields", {})
                # S4.5 P3#137：enrich 增加开关（extract.enrich 默认开，兼容现状）
                intelligence = (
                    enrich_records(outcome.records, self.config)
                    if extract_config.get("enrich", True)
                    else {"entities_resolved": 0, "near_duplicates": 0}
                )
                self.metrics.increment(
                    "omnicrawler_entities_resolved_total", intelligence["entities_resolved"]
                )
                self.metrics.increment(
                    "omnicrawler_near_duplicate_records_total", intelligence["near_duplicates"]
                )
                if isinstance(fields, dict) and fields:
                    self._stage_quality(run_id, outcome.records, fields, extract_config)
                semantic_changes = self.state.track_semantic_changes(run_id, outcome.records)
                self.metrics.increment("omnicrawler_semantic_changes_total", len(semantic_changes))
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
                self.metrics.increment("omnicrawler_records_total", len(outcome.records), processor=processor_name)
                self._emit(
                    "after_extract",
                    run_id=run_id,
                    result=result,
                    records=outcome.records,
                    count=len(outcome.records),
                    processor=parser_name or extractor_name or processor_name,
                )
            except Exception as exc:
                raise ExtractionError(f"{type(exc).__name__}: {exc}") from exc

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

    def _per_url_extract_override(
        self, result: FetchResult
    ) -> tuple[dict[str, Any] | None, Any | None]:
        """B-2 闸门：命中 ``source.seed_template_overrides`` 时返回覆盖后的提取配置。

        Returns:
            ``(extract_sec, temp_config)``：
            - 命中：``extract_sec`` 为「覆盖模板 extract 段 ⊕ 基础 extract 段」的合并结果
              （模板优先，base 未定义字段保留）；``temp_config`` 为仅本条文档生效的
              临时 AppConfig（供 ``_processor(config=...)`` 构建独立实例）。
            - 未命中 / 模板缺失 / 应用失败：``(None, None)``，调用方按原逻辑使用共享
              ``self.config``。

        线程安全：全程只读 ``self.config`` 并新建临时对象，不改共享状态，多线程抓取下安全。
        """
        raw_source = self.config.raw.get("source", {})
        if not isinstance(raw_source, dict):
            return None, None
        overrides = raw_source.get("seed_template_overrides", {})
        if not isinstance(overrides, dict) or not overrides:
            return None, None
        # 先按原始请求 URL 匹配（种子页/重定向前的 URL），再按最终 URL 兜底
        template_id = overrides.get(result.request.url)
        if template_id is None:
            template_id = overrides.get(result.final_url)
        if not template_id or not str(template_id).strip():
            return None, None
        template_id = str(template_id).strip()
        url = result.request.url or result.final_url
        try:
            from dataclasses import replace as _replace

            from ..core.utils import deep_merge
            from ..templates.template_catalog import bundled_template_catalog

            catalog = bundled_template_catalog()
            record = catalog.get(template_id)
            if record is None:
                LOGGER.warning("per-URL 覆盖模板 %r 不存在，URL %s 按默认提取", template_id, url)
                return None, None
            rendered = catalog.render(record, {"seed_url": url}, strict=False)
            tpl_extract = rendered.get("extract", {})
            if not isinstance(tpl_extract, dict):
                tpl_extract = {}
            merged = deep_merge(
                self.config.section("extract"), _strip_placeholders(tpl_extract)
            )
            temp_raw = dict(self.config.raw)
            temp_raw["extract"] = merged
            temp_config = _replace(self.config, raw=temp_raw)
            return merged, temp_config
        except Exception as exc:  # noqa: BLE001 —— 覆盖失败不阻断采集，回退默认提取
            LOGGER.warning("per-URL 覆盖 %s 应用失败，回退默认提取：%s", url, exc)
            return None, None

    def _capture_capsules(
        self,
        run_id: str,
        result: FetchResult,
        records: list[Any],
        extract_sec: dict[str, Any],
    ) -> None:
        """B-1 证据胶囊埋点：为每个字段的提取动作写一条胶囊（默认关闭）。

        门控：环境变量 ``OMNICRAWL_CAPSULE_ENABLED=true``。胶囊目录 = state 库
        同目录 ``capsules/``（与 replay 默认一致）。记录原始提取输入（URL/规则）
        与输出（dom_hash/值/证据），供 ``omnicrawler timeline`` 查看与
        ``omnicrawler replay`` 限定重放。任何异常只 warning，绝不阻断采集。
        """
        import os

        if str(os.environ.get("OMNICRAWL_CAPSULE_ENABLED", "")).casefold() != "true":
            return
        try:
            from hashlib import sha256

            from ..state.capsule_store import Capsule, CapsuleStore

            fields = extract_sec.get("fields", {})
            if not isinstance(fields, dict) or not fields:
                return
            store = CapsuleStore(Path(self.state.path).parent / "capsules")
            dom_hash = sha256(result.body).hexdigest()
            item_selector = str(extract_sec.get("item_selector", "") or "")
            for field_name, rule in fields.items():
                value: Any = None
                trace: dict[str, Any] | None = None
                for record in records:
                    data = getattr(record, "data", None)
                    if isinstance(data, dict) and field_name in data:
                        value = data[field_name]
                        evidence = getattr(record, "evidence", None)
                        if isinstance(evidence, dict):
                            trace = evidence.get(field_name)
                        break
                capsule = Capsule(
                    run_id=run_id,
                    action_type="extract_field",
                    action_name=str(field_name),
                    input={
                        "url": result.final_url,
                        "item_selector": item_selector,
                        "rule": rule,
                    },
                    output={
                        "dom_hash": dom_hash,
                        "value": value,
                        "trace": trace,
                    },
                    code_location="omnicrawler.pipeline._extract:_handle_result",
                )
                store.append(run_id, capsule)
        except Exception as exc:  # noqa: BLE001 —— 埋点失败不阻断采集
            LOGGER.warning("证据胶囊埋点跳过：%s", exc)

    def _normalize_records(self, records: list[Any]) -> None:
        """值级清洗（AutoDataCleaner 借鉴）：按 quality.normalize 配置执行。

        任何异常都只记 warning，绝不阻断采集主流程。reprocess 复用
        _handle_result 路径，自动获得相同清洗。
        """
        quality_cfg = self.config.section("quality")
        norm_cfg = quality_cfg.get("normalize", {})
        if not isinstance(norm_cfg, dict) or not norm_cfg.get("enabled", True):
            return
        if not records:
            return
        try:
            from ..quality.normalizers import normalize_records, policy_from_config

            fields = self.config.section("extract").get("fields", {})
            report = normalize_records(
                records,
                fields=fields if isinstance(fields, dict) else None,
                policy=policy_from_config(norm_cfg),
            )
            if report.total_changed:
                LOGGER.debug(
                    "值归一化：%d 个单元格变更（L1=%s L2=%s）：%s",
                    report.total_changed,
                    report.enabled_l1,
                    report.enabled_l2,
                    {f.name: f.kind for f in report.fields if f.changed_cells},
                )
        except Exception as exc:  # noqa: BLE001 —— 清洗失败不阻断采集
            LOGGER.warning("值归一化跳过：%s", exc)

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
        self.metrics.increment("omnicrawler_review_required_total", quality_summary["review_required"])
        self.metrics.increment("omnicrawler_duplicate_records_total", quality_summary["duplicates"])
        self.metrics.increment("omnicrawler_anomalies_total", quality_summary["anomalies"])
        self.state.add_quality_stats(run_id, quality_summary["field_stats"])
        for field_name, values in quality_summary["field_stats"].items():
            self.metrics.increment(
                "omnicrawler_field_present_total", values["present"], field=field_name
            )
            self.metrics.increment(
                "omnicrawler_field_missing_total",
                values["total"] - values["present"],
                field=field_name,
            )
            self.metrics.increment(
                "omnicrawler_field_invalid_total",
                values["present"] - values["valid"],
                field=field_name,
            )
