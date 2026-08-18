"""User-facing task specification and deterministic execution-plan compiler.

TaskSpec describes *what* a user wants.  The compiled mapping describes *how*
OmniCrawler will do it.  Keeping the two layers separate lets the GUI stay
friendly without hiding or weakening the reproducible YAML plan.
"""

from __future__ import annotations

import copy
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.utils import deep_merge


@dataclass(slots=True)
class TopicSpec:
    include_any: list[str] = field(default_factory=list)
    include_all: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    match_on: list[str] = field(
        default_factory=lambda: ["url", "anchor", "title", "heading", "text"]
    )
    keep_uncertain: bool = True


@dataclass(slots=True)
class FileSpec:
    enabled: bool = False
    extensions: list[str] = field(default_factory=lambda: [".pdf"])
    process_pdf: bool = False
    ocr: str = "auto"


@dataclass(slots=True)
class UpdateSpec:
    enabled: bool = False
    detect_same_url_changes: bool = True
    keep_versions: bool = True
    revisit_completed: bool = True


@dataclass(slots=True)
class AISpec:
    mode: str = "disabled"
    provider: str = ""
    capabilities: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TaskSpec:
    name: str = "新采集任务"
    intent: str = "auto"
    seeds: list[str] = field(default_factory=list)
    scope: str = "same_host"
    execution_mode: str = "auto"
    max_pages: int = 100
    max_depth: int = 3
    topic: TopicSpec = field(default_factory=TopicSpec)
    files: FileSpec = field(default_factory=FileSpec)
    updates: UpdateSpec = field(default_factory=UpdateSpec)
    ai: AISpec = field(default_factory=AISpec)
    outputs: list[str] = field(default_factory=lambda: ["jsonl", "csv", "xlsx"])

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> TaskSpec:
        data = copy.deepcopy(value)
        return cls(
            name=str(data.get("name", "新采集任务")),
            intent=str(data.get("intent", "auto")),
            seeds=[str(item) for item in data.get("seeds", [])],
            scope=str(data.get("scope", "same_host")),
            execution_mode=str(data.get("execution_mode", "auto")),
            max_pages=int(data.get("max_pages", 100)),
            max_depth=int(data.get("max_depth", 3)),
            topic=TopicSpec(**_known(TopicSpec, data.get("topic", {}))),
            files=FileSpec(**_known(FileSpec, data.get("files", {}))),
            updates=UpdateSpec(**_known(UpdateSpec, data.get("updates", {}))),
            ai=AISpec(**_known(AISpec, data.get("ai", {}))),
            outputs=[str(item) for item in data.get("outputs", ["jsonl", "csv", "xlsx"])],
        )

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.name.strip():
            errors.append("任务名称不能为空")
        if not self.seeds:
            errors.append("至少需要一个入口网址")
        for seed in self.seeds:
            if urllib.parse.urlsplit(seed).scheme not in {"http", "https"}:
                errors.append(f"入口网址必须以 http:// 或 https:// 开头: {seed}")
        if self.max_pages < 1:
            errors.append("最大页面数必须大于 0")
        if self.max_depth < 0:
            errors.append("最大层级不能为负数")
        if self.ai.mode not in {"disabled", "local", "cloud", "custom"}:
            errors.append("AI 模式只能是 disabled、local、cloud 或 custom")
        return errors


@dataclass(slots=True)
class ExecutionPlan:
    task: TaskSpec
    config: dict[str, Any]
    decisions: list[str]
    warnings: list[str]


def compile_execution_plan(task: TaskSpec, base: dict[str, Any] | None = None) -> ExecutionPlan:
    errors = task.validate()
    if errors:
        raise ValueError("；".join(errors))
    decisions: list[str] = []
    warnings: list[str] = []
    intent = task.intent.casefold()
    mode = task.execution_mode.casefold()
    if mode == "browser" or intent == "interactive":
        source_kind = "browser"
        decisions.append("使用可见浏览器执行登录、点击、搜索、滚动或动态渲染")
    elif mode == "api" or intent == "api":
        source_kind = "rest"
        decisions.append("直接执行已确认的数据接口，避免依赖地址栏翻页")
    elif task.updates.enabled:
        source_kind = "incremental"
        decisions.append("每轮重新访问已完成网址并比较内容版本")
    elif intent in {"documents", "records"} or task.files.enabled:
        source_kind = "focused"
        decisions.append("围绕栏目、主题词和附件链接进行定向遍历")
    else:
        source_kind = "crawl"
        decisions.append("从入口页自动识别结构并在同站范围内遍历")

    topic_enabled = bool(task.topic.include_any or task.topic.include_all or task.topic.exclude)
    extensions = [_normalize_extension(item) for item in task.files.extensions]
    if task.files.process_pdf and ".pdf" not in extensions:
        extensions.append(".pdf")
    focus = list(dict.fromkeys([
        *task.topic.include_any,
        *task.topic.include_all,
        "pdf" if ".pdf" in extensions else "",
        "下载" if task.files.enabled else "",
        "附件" if task.files.enabled else "",
    ]))
    focus = [item for item in focus if item]
    slug = _project_slug(task.name, task.seeds[0])
    generated: dict[str, Any] = {
        "config_version": 5,
        "task": task.to_mapping(),
        "project": {"name": slug, "workspace": f"work/{slug}", "intent": task.intent},
        "source": {"kind": source_kind, "seeds": task.seeds},
        "crawl": {
            "max_pages": task.max_pages,
            "max_depth": task.max_depth,
            "same_host": task.scope == "same_host",
            "focus_keywords": focus,
            "strategy": "priority" if focus else "bfs",
        },
        "selection": {"topic": {"enabled": topic_enabled, **asdict(task.topic)}},
        "download": {"enabled": task.files.enabled, "extensions": extensions},
        "processors": {
            "pdf": {
                "enabled": task.files.process_pdf,
            "config": "builtin:pdf/generic_template.yaml",
                "skip_ocr": task.files.ocr == "never",
                "ocr_backend": "none" if task.files.ocr in {"auto", "never"} else task.files.ocr,
            }
        },
        "updates": asdict(task.updates),
        "incremental": {"skip_unchanged": True, "archive_raw": True},
        "ai": {
            "mode": task.ai.mode,
            "default_provider": task.ai.provider,
            "routing": {capability: task.ai.provider for capability in task.ai.capabilities},
        },
        "outputs": {name: name in task.outputs for name in ("jsonl", "csv", "xlsx", "parquet", "duckdb")},
    }
    if task.files.enabled:
        decisions.append("先按栏目/主题筛选链接，再按响应类型确认并保存附件")
    if task.files.process_pdf:
        decisions.append("PDF 下载后进入文本提取；仅在需要时启用 OCR")
    if topic_enabled and task.topic.keep_uncertain:
        warnings.append("无法从链接判断主题的候选会先保留，读取正文后再判定，避免漏采")
    if task.ai.mode != "disabled":
        decisions.append("AI 只用于所选增强能力；抓取、去重和版本比较仍由确定性内核完成")
    config = deep_merge(base or {}, generated)
    return ExecutionPlan(task, config, decisions, warnings)


def _known(cls: type[Any], value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key in cls.__dataclass_fields__}


def _normalize_extension(value: str) -> str:
    value = value.strip().casefold()
    return value if value.startswith(".") else f".{value}"


def _project_slug(name: str, seed: str) -> str:
    host = urllib.parse.urlsplit(seed).hostname or "site"
    value = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff-]+", "_", name.strip()).strip("_")
    if not value or value == "新采集任务":
        value = re.sub(r"[^a-zA-Z0-9_-]+", "_", host).strip("_")
    return value[:64] or "omnicrawler_task"
