from __future__ import annotations

import copy
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.utils import deep_merge
from .task_spec import TaskSpec, compile_execution_plan

TASK_IR_VERSION = 1
_SECTIONS = (
    "identity", "goal", "source", "scope", "authorization", "actions", "pagination",
    "fields", "filters", "attachments", "pdf", "quality", "outputs", "updates",
    "resources", "capabilities",
)


@dataclass(slots=True)
class TaskIR:
    """Versioned, lossless task intermediate representation."""

    ir_version: int = TASK_IR_VERSION
    identity: dict[str, Any] = field(default_factory=dict)
    goal: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    authorization: dict[str, Any] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    pagination: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    attachments: dict[str, Any] = field(default_factory=dict)
    pdf: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    updates: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> TaskIR:
        """Construct a ``TaskIR`` from a raw mapping, validating section types.

        Unknown keys are preserved in ``extensions`` so no information is lost.
        """
        version = int(value.get("ir_version", TASK_IR_VERSION))
        if version > TASK_IR_VERSION:
            raise ValueError(f"Task IR v{version}高于当前支持的v{TASK_IR_VERSION}")
        known = {"ir_version", "extensions", *_SECTIONS}
        extensions = copy.deepcopy(value.get("extensions", {}))
        if not isinstance(extensions, dict):
            raise ValueError("Task IR extensions必须是对象")
        extensions.update({key: copy.deepcopy(item) for key, item in value.items() if key not in known})
        kwargs: dict[str, Any] = {}
        for name in _SECTIONS:
            raw = copy.deepcopy(value.get(name, [] if name in {"actions", "capabilities"} else {}))
            if name in {"actions", "capabilities"}:
                if not isinstance(raw, list):
                    raise ValueError(f"Task IR {name}必须是数组")
            elif not isinstance(raw, dict):
                raise ValueError(f"Task IR {name}必须是对象")
            kwargs[name] = raw
        kwargs["capabilities"] = [str(item) for item in kwargs["capabilities"]]
        return cls(version, extensions=extensions, **kwargs)

    @classmethod
    def from_task_spec(cls, task: TaskSpec, *, base: dict[str, Any] | None = None) -> TaskIR:
        """Compile a high-level :class:`TaskSpec` into a ``TaskIR`` instance."""
        return cls.from_config(compile_execution_plan(task, base).config)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> TaskIR:
        """Build a ``TaskIR`` from a flattened OmniCrawler config mapping.

        Sections are split into the canonical IR fields; the original config
        is retained in ``extensions["config_passthrough"]`` for lossless
        round-tripping via :meth:`to_config`.
        """
        raw = copy.deepcopy(dict(config))
        task = raw.get("task", {}) if isinstance(raw.get("task"), dict) else {}
        project = raw.get("project", {}) if isinstance(raw.get("project"), dict) else {}
        source = raw.get("source", {}) if isinstance(raw.get("source"), dict) else {}
        crawl = raw.get("crawl", {}) if isinstance(raw.get("crawl"), dict) else {}
        extract = raw.get("extract", {}) if isinstance(raw.get("extract"), dict) else {}
        browser = raw.get("browser", {}) if isinstance(raw.get("browser"), dict) else {}
        download = raw.get("download", {}) if isinstance(raw.get("download"), dict) else {}
        processors = raw.get("processors", {}) if isinstance(raw.get("processors"), dict) else {}
        pdf = processors.get("pdf", {}) if isinstance(processors.get("pdf"), dict) else {}
        capabilities = infer_capabilities(raw)
        return cls(
            identity={"name": project.get("name", task.get("name", "新采集任务")), "workspace": project.get("workspace", "")},
            goal={"intent": project.get("intent", task.get("intent", "auto")), "description": task.get("description", "")},
            source=source,
            scope={key: copy.deepcopy(crawl.get(key)) for key in ("same_host", "allow_domains", "deny_patterns", "allow_patterns", "max_pages", "max_depth") if key in crawl},
            authorization=copy.deepcopy(raw.get("auth", {})) if isinstance(raw.get("auth"), dict) else {},
            actions=copy.deepcopy(browser.get("actions", [])) if isinstance(browser.get("actions", []), list) else [],
            pagination=copy.deepcopy(source.get("pagination", {})) if isinstance(source.get("pagination"), dict) else {},
            fields=copy.deepcopy(extract.get("fields", {})) if isinstance(extract.get("fields"), dict) else {},
            filters=copy.deepcopy(raw.get("selection", {})) if isinstance(raw.get("selection"), dict) else {},
            attachments=download,
            pdf=pdf,
            quality={"extract": extract, "data_quality": copy.deepcopy(raw.get("data_quality", {}))},
            outputs=copy.deepcopy(raw.get("outputs", {})) if isinstance(raw.get("outputs"), dict) else {},
            updates=copy.deepcopy(raw.get("updates", {})) if isinstance(raw.get("updates"), dict) else {},
            resources=copy.deepcopy(raw.get("resources", {})) if isinstance(raw.get("resources"), dict) else {},
            capabilities=capabilities,
            extensions={"config_passthrough": raw},
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialize the IR to a plain dict suitable for persistence or merge."""
        result: dict[str, Any] = {"ir_version": self.ir_version}
        for name in _SECTIONS:
            result[name] = copy.deepcopy(getattr(self, name))
        result["extensions"] = copy.deepcopy(self.extensions)
        return result

    def to_config(self, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Reconstruct a flattened OmniCrawler config from the IR.

        Args:
            base: Optional base config merged underneath the IR-derived values.
        """
        passthrough = self.extensions.get("config_passthrough", {})
        config = deep_merge(copy.deepcopy(dict(base or {})), passthrough if isinstance(passthrough, dict) else {})
        source = deep_merge(config.get("source", {}), self.source)
        if self.pagination:
            source["pagination"] = copy.deepcopy(self.pagination)
        extract = copy.deepcopy(self.quality.get("extract", {})) if isinstance(self.quality.get("extract"), dict) else {}
        extract["fields"] = copy.deepcopy(self.fields)
        project = deep_merge(config.get("project", {}), self.identity)
        if self.goal.get("intent"):
            project["intent"] = self.goal["intent"]
        crawl = deep_merge(config.get("crawl", {}), self.scope)
        browser = deep_merge(config.get("browser", {}), {"actions": copy.deepcopy(self.actions)})
        processors = deep_merge(config.get("processors", {}), {"pdf": copy.deepcopy(self.pdf)})
        generated = {
            "config_version": 5,
            "project": project,
            "source": source,
            "crawl": crawl,
            "auth": copy.deepcopy(self.authorization),
            "browser": browser,
            "extract": extract,
            "selection": copy.deepcopy(self.filters),
            "download": copy.deepcopy(self.attachments),
            "processors": processors,
            "data_quality": copy.deepcopy(self.quality.get("data_quality", {})),
            "outputs": copy.deepcopy(self.outputs),
            "updates": copy.deepcopy(self.updates),
            "resources": copy.deepcopy(self.resources),
        }
        return deep_merge(config, generated)

    def merge_fragment(self, fragment: Mapping[str, Any]) -> TaskIR:
        """Return a new IR with ``fragment`` deep-merged on top of this one."""
        return TaskIR.from_mapping(deep_merge(self.to_mapping(), copy.deepcopy(dict(fragment))))


def infer_capabilities(config: Mapping[str, Any]) -> list[str]:
    """Derive declared capability tags (browser, ocr, ai, storage, plugin, stream)."""
    result: list[str] = []
    source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    browser = config.get("browser", {}) if isinstance(config.get("browser"), dict) else {}
    processors = config.get("processors", {}) if isinstance(config.get("processors"), dict) else {}
    pdf = processors.get("pdf", {}) if isinstance(processors.get("pdf"), dict) else {}
    ai = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    storage = config.get("storage", {}) if isinstance(config.get("storage"), dict) else {}
    plugins = config.get("plugins", {}) if isinstance(config.get("plugins"), dict) else {}
    if source.get("kind") == "browser" or browser.get("actions"):
        result.append("browser")
    if pdf.get("enabled") and not pdf.get("skip_ocr"):
        result.append("ocr")
    if ai.get("mode", "disabled") != "disabled":
        result.append("ai")
    if storage and (storage.get("objects", {}).get("backend", "local") != "local" or storage.get("records", {}).get("backends")):
        result.append("storage")
    if plugins.get("paths"):
        result.append("plugin")
    if source.get("kind") in {"websocket", "sse", "long_poll"}:
        result.append("stream")
    return list(dict.fromkeys(result))


def source_domains(ir: TaskIR) -> list[str]:
    """Extract unique sorted hostnames from the IR's seeds and login URL."""
    values = [*ir.source.get("seeds", []), ir.authorization.get("url", "")]
    domains: set[str] = set()
    for value in values:
        host = urllib.parse.urlsplit(str(value)).hostname
        if host:
            domains.add(host.casefold())
    return sorted(domains)


def recording_fragment(recording: Mapping[str, Any]) -> dict[str, Any]:
    """Compile action-recorder output into a mergeable IR fragment."""

    raw_actions = recording.get("actions", [])
    if not isinstance(raw_actions, list):
        raise ValueError("录制结果actions必须是数组")
    actions = [copy.deepcopy(item) for item in raw_actions if isinstance(item, dict)]
    fragment: dict[str, Any] = {"actions": actions, "capabilities": ["browser"] if actions else []}
    start_url = str(recording.get("start_url", "")).strip()
    if start_url:
        fragment["source"] = {"kind": "browser", "seeds": [start_url]}
    captures = recording.get("api_responses", [])
    if isinstance(captures, list) and captures:
        fragment.setdefault("extensions", {})["api_candidates"] = copy.deepcopy(captures)
    return fragment


def api_candidate_fragment(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Compile an API-discovery candidate without discarding browser fallback evidence."""

    url = str(candidate.get("url", "")).strip()
    if not url:
        raise ValueError("API候选缺少url")
    source: dict[str, Any] = {
        "kind": "rest",
        "seeds": [url],
        "method": str(candidate.get("method", "GET")).upper(),
    }
    for key in ("headers", "body", "item_path", "pagination"):
        if key in candidate:
            source[key] = copy.deepcopy(candidate[key])
    fragment = {"source": source, "extensions": {"api_candidate_evidence": copy.deepcopy(dict(candidate))}}
    if isinstance(candidate.get("pagination"), dict):
        fragment["pagination"] = copy.deepcopy(candidate["pagination"])
    return fragment


def template_fragment(template_config: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a full/partial template config to the same IR merge contract."""

    ir = TaskIR.from_config(template_config)
    fragment = ir.to_mapping()
    fragment["extensions"].pop("config_passthrough", None)
    return fragment
