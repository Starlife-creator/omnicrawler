from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from dataclasses import asdict, dataclass
from typing import Any

from ..core.config import AppConfig, validate_config
from ..runtime.resource_profiles import profile_for
from ..security.security_audit import scan_config_file


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    code: str
    status: str
    title: str
    message: str
    fix: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_preflight(config: AppConfig) -> dict[str, Any]:
    checks: list[PreflightCheck] = []
    errors, warnings = validate_config(config)
    for index, message in enumerate(errors, 1):
        checks.append(PreflightCheck(f"config_error_{index}", "error", "配置错误", message))
    for index, message in enumerate(warnings, 1):
        checks.append(PreflightCheck(f"config_warning_{index}", "warning", "配置提醒", message))
    secret_scan = scan_config_file(config.path)
    for finding in secret_scan["findings"]:
        checks.append(
            PreflightCheck(
                f"plaintext_secret_{finding['line']}",
                "error",
                "明文凭据",
                f"配置第 {finding['line']} 行：{finding['message']}",
                {"action": "open_config", "line": finding["line"]},
            )
        )

    parent = config.workspace.parent if config.workspace.parent.exists() else config.root
    usage = shutil.disk_usage(parent)
    minimum = int(config.section("resources").get("minimum_free_disk_bytes", 536_870_912))
    checks.append(
        PreflightCheck(
            "disk_space",
            "ok" if usage.free >= minimum else "error",
            "磁盘空间",
            f"可用 {usage.free / 1024 ** 3:.2f} GB；安全保留 {minimum / 1024 ** 3:.2f} GB",
            {"action": "open_folder", "path": str(parent)} if usage.free < minimum else None,
        )
    )
    requirements = _required_dependencies(config)
    for label, module, install_hint in requirements:
        available = importlib.util.find_spec(module) is not None
        checks.append(
            PreflightCheck(
                f"dependency_{label}",
                "ok" if available else "error",
                f"依赖：{label}",
                "已安装" if available else f"未安装；{install_hint}",
                {"action": "install", "extra": install_hint} if not available else None,
            )
        )
    concurrency = int(config.section("crawl").get("concurrency", 4))
    delay = float(config.section("http").get("delay_seconds", 1.0))
    if concurrency > 8 and delay < 0.25:
        checks.append(
            PreflightCheck(
                "aggressive_rate",
                "warning",
                "访问速度偏高",
                "高并发和低延迟同时启用，容易触发限流并占满笔记本资源。",
                {"action": "patch_config", "patch": {"crawl.concurrency": 4, "http.delay_seconds": 1.0}},
            )
        )
    profile = profile_for(config)
    maximum_pages = int(config.section("crawl").get("max_pages", 100))
    estimated_seconds = maximum_pages * max(delay, 0.2) / max(1, min(concurrency, profile.concurrency_cap))
    estimate = {
        "maximum_pages": maximum_pages,
        "resource_profile": profile.to_dict(),
        "estimated_minimum_seconds": round(estimated_seconds, 1),
        "estimated_raw_storage_mb": round(maximum_pages * 0.75, 1),
        "sample_pages_recommended": min(3, maximum_pages),
    }
    error_count = sum(item.status == "error" for item in checks)
    warning_count = sum(item.status == "warning" for item in checks)
    return {
        "ok": error_count == 0,
        "checks": [item.to_dict() for item in checks],
        "errors": error_count,
        "warnings": warning_count,
        "estimate": estimate,
    }


def run_sample(config: AppConfig, *, pages: int = 3) -> dict[str, Any]:
    """Run a disposable small crawl without changing the main task state."""

    from ..pipeline import Pipeline

    raw = copy.deepcopy(config.raw)
    sample_workspace = config.workspace / "preflight_samples" / "latest"
    raw.setdefault("project", {})["workspace"] = str(sample_workspace)
    raw.setdefault("crawl", {})["max_pages"] = max(1, min(10, int(pages)))
    sample_config = AppConfig(config.path, config.root, raw, sample_workspace)
    with Pipeline(sample_config) as pipeline:
        result = pipeline.run(max_pages=max(1, min(10, int(pages))))
    output = config.workspace / "preflight_sample.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"sample": result, "report": str(output)}


def _required_dependencies(config: AppConfig) -> list[tuple[str, str, str]]:
    result = [("PyYAML", "yaml", "pip install PyYAML")]
    if config.source_kind == "browser":
        result.append(("Playwright", "playwright", "pip install omnicrawl-platform[browser]"))
    if config.section("processors").get("pdf", {}).get("enabled"):
        result.extend(
            [
                ("PyMuPDF", "fitz", "pip install omnicrawl-platform[pdf]"),
                ("openpyxl", "openpyxl", "pip install omnicrawl-platform[pdf]"),
            ]
        )
    return result
