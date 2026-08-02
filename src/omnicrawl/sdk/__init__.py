"""OmniCrawler public SDK preview.

Only names exported here are public. ``stable`` APIs receive a deprecation period;
``preview`` APIs may evolve between minor releases. GUI/database internals are not exposed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..pipeline_ops.plan_compiler import TaskPlan, compile_task_plan
from ..pipeline_ops.task_ir import TaskIR
from ..pipeline_ops.task_spec import TaskSpec
from ..services.application_service import ApplicationService
from .data import ArtifactInfo, DatasetReader
from .protocols import CredentialProvider, Exporter, Extractor, Fetcher, Processor, Source

SDK_VERSION = "1.0-preview"
API_STABILITY = {
    "TaskSpec": "stable", "TaskIR": "stable", "TaskPlan": "stable",
    "validate": "stable", "compile": "stable", "run": "preview",
    "query": "preview", "protocols": "preview",
}


def validate(config_path: str | Path) -> dict[str, Any]:
    """Validate a YAML configuration file without executing it.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Validation result dict with keys ``valid`` (bool), ``errors`` (list[str]),
        and ``warnings`` (list[str]).

    Raises:
        FileNotFoundError: If *config_path* does not exist.
        yaml.YAMLError: If the file is not valid YAML.

    Stability: stable
    """
    return ApplicationService(config_path).validate()


def compile(config_path: str | Path, *, capabilities: list[str] | None = None) -> dict[str, Any]:
    """Compile a configuration into a :class:`TaskPlan` without running it.

    Args:
        config_path: Path to the YAML configuration file.
        capabilities: Optional list of available capability strings
            (e.g. ``["browser", "pdf"]``).  When *None*, all capabilities
            declared in the config are assumed available.

    Returns:
        Compilation result dict with keys ``plan`` (TaskPlan dict),
        ``ir`` (TaskIR dict), and ``warnings`` (list[str]).

    Raises:
        ValueError: If the configuration references unknown source types
            or required capabilities are not satisfied.

    Stability: stable
    """
    return ApplicationService(config_path).compile(available_capabilities=capabilities)


def run(config_path: str | Path, *, resume: bool = False, require_sample_match: bool = True) -> dict[str, Any]:
    """Execute a crawl task defined by *config_path*.

    Args:
        config_path: Path to the YAML configuration file.
        resume: If *True*, attempt to resume a previously interrupted run
            using the seven-state recovery center.
        require_sample_match: If *True*, abort when the first fetched page
            does not match the field selectors defined in the config.

    Returns:
        Run result dict with keys ``run_id`` (str), ``status`` (str),
        ``stats`` (dict), and ``records`` (int).

    Raises:
        RuntimeError: If the pipeline encounters a fatal error.
        ValueError: If *require_sample_match* is *True* and the sample
            page does not match the configured selectors.

    Stability: preview
    """
    return ApplicationService(config_path).run(resume=resume, require_sample_match=require_sample_match)


def query(config_path: str | Path) -> dict[str, Any]:
    """Query the status and results of the latest run for *config_path*.

    Args:
        config_path: Path to the YAML configuration file whose workspace
            should be queried.

    Returns:
        Query result dict with keys ``run_id`` (str | None),
        ``status`` (str), ``stats`` (dict), ``records`` (list[dict]),
        and ``artifacts`` (list[dict]).

    Stability: preview
    """
    return ApplicationService(config_path).query()


__all__ = [
    "API_STABILITY", "SDK_VERSION", "ApplicationService", "ArtifactInfo", "CredentialProvider", "DatasetReader", "Exporter", "Extractor",
    "Fetcher", "Processor", "Source", "TaskIR", "TaskPlan", "TaskSpec", "compile", "compile_task_plan",
    "query", "run", "validate",
]
