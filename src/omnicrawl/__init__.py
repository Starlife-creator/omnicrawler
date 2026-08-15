"""OmniCrawler：可配置、可恢复、可扩展的数据采集与文档抽取平台。

Public API::

    from omnicrawl import AppConfig, Pipeline, StateStore
"""

from __future__ import annotations

import importlib
import importlib.abc
import logging
from typing import Any

# Keep this value import-safe for source checkouts. Packaging metadata is
# verified against it by tools/check_docs_consistency.py before release.
__version__ = "0.8.0"

logger = logging.getLogger(__name__)

# -- 核心公开 API (stable) ------------------------------------------------
__all__ = [
    "__version__",
    "AppConfig",
    "Pipeline",
    "StateStore",
]


# -- 向后兼容：旧路径自动重导到新子包 --
_DEPRECATED_MODULE_MAP = {
    "action_recorder": "fetching.action_recorder",
    "adaptive_execution": "runtime.adaptive_execution",
    "ai_providers": "services.ai_providers",
    "ai_safety": "services.ai_safety",
    "ai_task_designer": "services.ai_task_designer",
    "api_discovery": "extraction.api_discovery",
    "apify_templates": "templates.apify_templates",
    "application_service": "services.application_service",
    "archives": "fetching.archives",
    "artifact_integrity": "quality.artifact_integrity",
    "auto_apply": "quality.auto_apply",
    "llm_candidate_generator": "quality.llm_candidate_generator",
    "async_fetcher": "fetching.async_fetcher",
    "auto_pilot": "runtime.auto_pilot",
    "benchmark_corpus": "services.benchmark_corpus",
    "benchmarking": "services.benchmarking",
    "browser_fetcher": "fetching.browser_fetcher",
    "capabilities": "core.capabilities",
    "cli_commands": "cli._handlers",
    "captcha_ocr": "fetching.captcha_ocr",
    "component_manager": "services.component_manager",
    "config": "core.config",
    "config_history": "services.config_history",
    "controllers": "services.controllers",
    "crawl4ai_bridge": "sources.crawl4ai_bridge",
    "credentials": "core.credentials",
    "data_governance": "security.data_governance",
    "data_intelligence": "quality.data_intelligence",
    "diagnostic_experience": "quality.diagnostic_experience",
    "diagnostics": "quality.diagnostics",
    "doctor": "services.doctor",
    "easyspider_bridge": "sources.easyspider_bridge",
    "egress": "security.egress",
    "error_center": "quality.error_center",
    "errors": "core.errors",
    "exporters": "pipeline.exporters",
    "evidence_ledger": "quality.evidence_ledger",
    "execution_backend": "runtime.execution_backend",
    "extractors": "extraction.extractors",
    "field_designer": "extraction.field_designer",
    "field_spec": "services.field_spec",
    "frameworks": "sources.frameworks",
    "help_registry": "services.help_registry",
    "html_tools": "extraction.html_tools",
    "http_client": "fetching.http_client",
    "intelligent_scraper": "extraction.intelligent_scraper",
    "logging_utils": "core.logging_utils",
    "metrics": "services.metrics",
    "migrations": "core.migrations",
    "models": "core.models",
    "natural_language_task": "services.natural_language_task",
    "offline_demo": "services.offline_demo",
    "pdf_integration": "pipeline_ops.pdf_integration",
    "pdf_region": "pipeline_ops.pdf_region",
    "pipeline_stages": "pipeline_ops.pipeline_stages",
    "plan_compiler": "pipeline_ops.plan_compiler",
    "plugin_inspector": "plugins.plugin_inspector",
    "plugin_runtime": "plugins.plugin_runtime",
    "plugin_sandbox": "plugins.plugin_sandbox",
    "plugin_sdk": "plugins.plugin_sdk",
    "plugin_subprocess": "plugins.plugin_subprocess",
    "plugins": "plugins.plugins",
    "policy": "security.policy",
    "preflight": "pipeline_ops.preflight",
    "product_metrics": "services.product_metrics",
    "provenance": "pipeline_ops.provenance",
    "quality": "quality.quality",
    "quality_report": "quality.quality_report",
    "recipe_engine": "templates.recipe_engine",
    "record_sinks": "services.record_sinks",
    "recovery": "runtime.recovery",
    "redis_frontier": "runtime.redis_frontier",
    "regression_library": "services.regression_library",
    "release_reliability": "services.release_reliability",
    "repository": "runtime.repository",
    "research_package": "services.research_package",
    "resource_profiles": "runtime.resource_profiles",
    "resources": "runtime.resources",
    "retention": "services.retention",
    "retry": "fetching.retry",
    "review_feedback": "review.review_feedback",
    "review_workbench": "review.review_workbench",
    "routing": "fetching.routing",
    "run_compare": "review.run_compare",
    "run_control": "runtime.run_control",
    "run_state": "core.run_state",
    "runtime_manifest": "core.runtime_manifest",
    "runtime_paths": "core.runtime_paths",
    "schedule_conditions": "runtime.schedule_conditions",
    "scheduler": "runtime.scheduler",
    "schema_registry": "quality.schema_registry",
    "security_audit": "security.security_audit",
    "semantic_changes": "quality.semantic_changes",
    "server": "services.server",
    "session": "fetching.session",
    "shadow_repair": "quality.shadow_repair",
    "site_adapters": "sources.site_adapters",
    "site_inspector": "sources.site_inspector",
    "sources": "sources.sources",
    "stealth_enhanced": "fetching.stealth_enhanced",
    "storage_backends": "services.storage_backends",
    "streams": "fetching.streams",
    "task_ir": "pipeline_ops.task_ir",
    "task_spec": "pipeline_ops.task_spec",
    "template_application": "templates.template_application",
    "template_catalog": "templates.template_catalog",
    "template_diff": "templates.template_diff",
    "template_health": "templates.template_health",
    "template_monitor": "templates.template_monitor",
    "temporal_facts": "quality.temporal_facts",
    "topic_filter": "extraction.topic_filter",
    "tls_impersonator": "fetching.tls_impersonator",
    "updater": "services.updater",
    "utils": "core.utils",
    "ux_service": "services.ux_service",
    "workbench": "services.workbench",
    "worker_main": "runtime.worker_main",
    "workspace": "services.workspace",
}

def _setup_compat_aliases() -> None:
    """注册向后兼容模块别名，使 `from omnicrawl.config import AppConfig` 仍可用。

    S4.1：不再于包加载时调用——eager 注册会导入全部兼容模块（约 287ms）。
    由模块级 __getattr__ 与 _CompatMetaFinder 惰性接管。
    """
    import importlib
    import sys

    for old_name, new_subpath in _DEPRECATED_MODULE_MAP.items():
        old_full = f"omnicrawl.{old_name}"
        if old_full not in sys.modules:
            try:
                module = importlib.import_module(f"omnicrawl.{new_subpath}")
                sys.modules[old_full] = module
            except Exception:
                logger.debug("Failed to import optional compat module '%s'", old_name, exc_info=True)


class _AliasLoader(importlib.abc.Loader):
    """S4.1：为已加载的壳模块提供兼容 spec。

    模块 __dict__ 只读用于外壳占位，真实命名空间由 Loader 填充；
    monkeypatch 通过模块级 __getattr__ 与实际导入都会得到真实模块，
    行为保持一致。
    """

    def __init__(self, module: Any) -> None:
        self._module = module

    def create_module(self, spec: Any) -> None:
        return None  # 由 bootstrap 重建模块

    def exec_module(self, module: Any) -> None:
        module.__dict__.clear()
        module.__dict__.update(self._module.__dict__)


class _CompatMetaFinder:
    """S4.1：拦截 `from omnicrawl.<旧名> import X` 形式的子模块导入，
    惰性重定向到新路径（import 语句不触发模块级 __getattr__）。"""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith("omnicrawl."):
            return None
        short = fullname[len("omnicrawl."):]
        if "." in short:
            return None
        import importlib.machinery

        # 物理上真实存在的子包/模块（quality/utils/state 等与旧别名同名）
        # 不被拦截——只有旧名路径不存在时才走兼容重定向
        if importlib.machinery.PathFinder.find_spec(fullname, path) is not None:
            return None
        new_subpath = _DEPRECATED_MODULE_MAP.get(short)
        if new_subpath is None:
            return None
        import importlib
        import importlib.util

        new_path = f"omnicrawl.{new_subpath}"
        module = importlib.import_module(new_path)
        return importlib.util.spec_from_loader(fullname, _AliasLoader(module))


def __getattr__(name: str):
    """模块级兼容重定向：旧 import 路径仍可用。

    例如 `from omnicrawl.config import AppConfig` 会自动重定向到
    `from omnicrawl.core.config import AppConfig`。
    """
    import importlib
    import sys

    if name in _DEPRECATED_MODULE_MAP:
        new_path = f"omnicrawl.{_DEPRECATED_MODULE_MAP[name]}"
        try:
            module = importlib.import_module(new_path)
        except Exception as exc:  # noqa: BLE001 - S4.1：失败记 warning 而非静默
            logger.warning("兼容模块 %s 加载失败: %s", new_path, exc)
            raise AttributeError(f"module 'omnicrawl' has no attribute {name!r}") from exc
        sys.modules[f"omnicrawl.{name}"] = module
        return module

    # 原有的延迟导入逻辑
    if name == "AppConfig":
        from .core.config import AppConfig
        return AppConfig
    if name == "Pipeline":
        from .pipeline import Pipeline
        return Pipeline
    if name == "StateStore":
        from .state import StateStore
        return StateStore

    raise AttributeError(f"module 'omnicrawl' has no attribute {name!r}")


# S4.1：惰性兼容重定向——不再 eager 导入全部兼容模块（import omnicrawl 毫秒级）
import sys as _sys

_sys.meta_path.insert(0, _CompatMetaFinder())
