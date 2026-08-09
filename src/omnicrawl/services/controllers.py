from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..pipeline_ops.task_ir import TaskIR, template_fragment
from ..templates.template_catalog import TemplateProbe, bundled_template_catalog
from .application_service import ApplicationService

logger = logging.getLogger(__name__)


def _error(message: str, *, status: str = "error") -> dict[str, Any]:
    """Construct a user-friendly error dict."""
    return {"error": message, "status": status}


class TaskController:
    def __init__(self, service: ApplicationService) -> None:
        self.service = service

    def load(self) -> dict[str, Any]:
        try:
            result = self.service.load()
            return result
        except FileNotFoundError as exc:
            logger.warning("Config not found: %s", exc)
            return _error(f"配置文件不存在: {exc}")
        except Exception as exc:
            logger.error("Load failed: %s", exc)
            return _error(f"加载失败: {exc}")

    def validate(self) -> dict[str, Any]:
        try:
            return self.service.validate()
        except Exception as exc:
            logger.error("Validate failed: %s", exc)
            return _error(f"验证失败: {exc}")

    def compile(self, capabilities: list[str] | None = None) -> dict[str, Any]:
        if capabilities is not None and not isinstance(capabilities, list):
            logger.warning("compile: capabilities should be a list or None, got %s", type(capabilities).__name__)
            return _error("capabilities 参数必须是列表或 None")
        try:
            return self.service.compile(available_capabilities=capabilities)
        except KeyError as exc:
            logger.error("Compile unknown capability: %s", exc)
            return _error(f"未知的能力标识: {exc}")
        except Exception as exc:
            logger.error("Compile failed: %s", exc)
            return _error(f"编译失败: {exc}")

    def compare(self, other: str | Path) -> dict[str, Any]:
        if not other:
            logger.warning("compare: other path is empty")
            return _error("比较目标路径不能为空")
        try:
            path = Path(other) if not isinstance(other, Path) else other
            if not path.exists():
                logger.warning("Compare target not found: %s", path)
                return _error(f"比较目标不存在: {path}")
            return self.service.diff(other)
        except FileNotFoundError as exc:
            logger.warning("Compare target not found: %s", exc)
            return _error(f"比较目标不存在: {exc}")
        except Exception as exc:
            logger.error("Compare failed: %s", exc)
            return _error(f"对比失败: {exc}")


class RunController:
    def __init__(self, service: ApplicationService) -> None:
        self.service = service

    def sample(self, pages: int = 3) -> dict[str, Any]:
        if not isinstance(pages, int) or pages < 1:
            logger.warning("sample: invalid pages=%s", pages)
            return _error("pages 参数必须是正整数")
        try:
            return self.service.sample(pages=pages)
        except Exception as exc:
            logger.error("Sample failed: %s", exc)
            return _error(f"采样运行失败: {exc}")

    def run(self, *, require_sample_match: bool = False) -> dict[str, Any]:
        try:
            return self.service.run(require_sample_match=require_sample_match)
        except FileNotFoundError as exc:
            logger.warning("Run config not found: %s", exc)
            return _error(f"配置文件不存在: {exc}")
        except PermissionError as exc:
            logger.error("Run permission denied: %s", exc)
            return _error(f"权限不足: {exc}")
        except Exception as exc:
            logger.error("Run failed: %s", exc)
            return _error(f"运行失败: {exc}")

    def pause(self) -> dict[str, Any]:
        try:
            return self.service.pause()
        except Exception as exc:
            logger.error("Pause failed: %s", exc)
            return _error(f"暂停失败: {exc}")

    def resume(self) -> dict[str, Any]:
        try:
            return self.service.resume()
        except Exception as exc:
            logger.error("Resume failed: %s", exc)
            return _error(f"恢复运行失败: {exc}")

    def stop(self) -> dict[str, Any]:
        try:
            return self.service.stop()
        except Exception as exc:
            logger.error("Stop failed: %s", exc)
            return _error(f"停止失败: {exc}")


class TemplateController:
    def __init__(self) -> None:
        self.catalog = bundled_template_catalog()

    def search(self, query: str = "", *, category: str = "") -> list[dict[str, Any]]:
        if not isinstance(query, str):
            logger.warning("search: query should be a string, got %s", type(query).__name__)
            return [{"error": "搜索关键字必须是字符串", "status": "error"}]
        try:
            return [asdict(record.metadata) for record in self.catalog.search(query, category=category)]
        except Exception as exc:
            logger.error("Template search failed: %s", exc)
            return [{"error": f"搜索模板失败: {exc}", "status": "error"}]

    def recommend(self, url: str, *, intent: str = "") -> list[dict[str, Any]]:
        if not url or not isinstance(url, str):
            logger.warning("recommend: url is empty or not a string")
            return [{"error": "URL 不能为空", "status": "error"}]
        try:
            return [
                {"template": asdict(match.record.metadata), "score": match.score, "reasons": list(match.reasons)}
                for match in self.catalog.recommend(TemplateProbe(url=url), intent=intent)
            ]
        except Exception as exc:
            logger.error("Template recommend failed: %s", exc)
            return [{"error": f"推荐模板失败: {exc}", "status": "error"}]

    def apply(self, ir: Mapping[str, Any], template_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        if not ir:
            return _error("IR 映射不能为空")
        if not template_id or not isinstance(template_id, str):
            return _error("template_id 不能为空且必须是字符串")
        if not isinstance(values, Mapping):
            return _error("values 必须是映射类型")
        try:
            rendered = self.catalog.render(template_id, values)
            return TaskIR.from_mapping(ir).merge_fragment(template_fragment(rendered)).to_mapping()
        except KeyError as exc:
            logger.error("Template not found: %s", exc)
            return _error(f"模板不存在: {exc}")
        except Exception as exc:
            logger.error("Template apply failed: %s", exc)
            return _error(f"应用模板失败: {exc}")


class ResultController:
    def __init__(self, service: ApplicationService) -> None:
        self.service = service

    def query(self) -> dict[str, Any]:
        try:
            return self.service.query()
        except FileNotFoundError as exc:
            logger.warning("No results found: %s", exc)
            return _error(f"未找到运行结果: {exc}")
        except Exception as exc:
            logger.error("Query results failed: %s", exc)
            return _error(f"查询结果失败: {exc}")

    def export(self, run_id: str | None = None) -> dict[str, Any]:
        if run_id is not None and not isinstance(run_id, str):
            logger.warning("export: run_id should be a string or None, got %s", type(run_id).__name__)
            return _error("run_id 参数必须是字符串或 None")
        try:
            return self.service.export(run_id)
        except FileNotFoundError as exc:
            logger.warning("Export target not found: %s", exc)
            return _error(f"导出目标不存在: {exc}")
        except PermissionError as exc:
            logger.error("Export permission denied: %s", exc)
            return _error(f"导出权限不足: {exc}")
        except Exception as exc:
            logger.error("Export failed: %s", exc)
            return _error(f"导出失败: {exc}")
