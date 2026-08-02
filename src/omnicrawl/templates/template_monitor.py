from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from ..core.config import AppConfig
from ..core.models import ExtractedRecord, FetchResult
from ..core.utils import atomic_write, safe_filename
from .template_health import StructureSnapshot


@dataclass(frozen=True, slots=True)
class TemplateObservation:
    template_id: str
    source_url: str
    status: str
    structure_similarity: float
    field_success: float
    invalidated: bool
    suggestions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TemplateMonitor:
    """Low-cost drift monitor that works from the HTML and extraction already in memory."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.directory = config.workspace / "template_health"
        self.observations: list[TemplateObservation] = []

    def observe(
        self,
        result: FetchResult,
        records: list[ExtractedRecord],
        fields: dict[str, Any],
    ) -> TemplateObservation | None:
        if "html" not in result.content_type and b"<html" not in result.body[:4096].lower():
            return None
        template_id = str(
            self.config.section("project").get("template_id")
            or self.config.section("template").get("id")
            or self.config.project_name
        )
        successes = {
            str(name): (
                sum(record.data.get(str(name)) not in (None, "", []) for record in records)
                / max(1, len(records))
            )
            for name in fields
        }
        html = result.body.decode("utf-8", errors="replace")
        current = StructureSnapshot.from_html(template_id, result.final_url, html, successes)
        snapshot_path = self.directory / f"{safe_filename(template_id)}.json"
        previous = StructureSnapshot.load(snapshot_path) if snapshot_path.is_file() else None
        similarity = current.similarity(previous) if previous else 1.0
        field_success = sum(successes.values()) / max(1, len(successes)) if successes else 1.0
        if similarity >= 0.75 and field_success >= 0.7:
            status = "healthy"
        elif similarity >= 0.45 and field_success >= 0.4:
            status = "warning"
        else:
            status = "invalid"
        suggestions: list[str] = []
        if similarity < 0.75:
            suggestions.append("页面结构发生变化，请重新运行可视化选字段")
        if field_success < 0.7:
            suggestions.append("关键字段成功率下降，请检查选择器或启用浏览器模式")
        if status == "invalid":
            suggestions.append("当前模板已标记失效；先使用通用自动提取作为回退")
        observation = TemplateObservation(
            template_id,
            result.final_url,
            status,
            round(similarity, 4),
            round(field_success, 4),
            status == "invalid",
            tuple(suggestions),
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        current.save(snapshot_path)
        history = self.directory / "observations.jsonl"
        with history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(observation.to_dict(), ensure_ascii=False) + "\n")
        atomic_write(
            self.directory / "latest.json",
            json.dumps(observation.to_dict(), ensure_ascii=False, indent=2).encode("utf-8"),
        )
        self.observations.append(observation)
        return observation

    def summary(self) -> dict[str, Any]:
        return {
            "observations": len(self.observations),
            "warnings": sum(item.status == "warning" for item in self.observations),
            "invalid": sum(item.invalidated for item in self.observations),
            "latest": self.observations[-1].to_dict() if self.observations else None,
            "directory": str(self.directory),
        }
