"""Consistent, actionable diagnostics for desktop, CLI and SDK consumers."""

from __future__ import annotations

import json
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .diagnostics import diagnose as _diagnose
from .diagnostics import redact_diagnostic_text, redact_diagnostic_value


@dataclass(frozen=True, slots=True)
class UserFacingDiagnostic:
    category: str
    what_happened: str
    possible_causes: tuple[str, ...]
    attempts: tuple[str, ...]
    actions: tuple[str, ...]
    data_impact: str
    recoverable: bool
    help_id: str


def diagnose(message: str, attempts: tuple[str, ...] = ()) -> UserFacingDiagnostic:
    """Compatibility DTO backed by the shared diagnostics engine."""
    report = _diagnose(message)
    legacy_category = {
        "rate_limited": "rate_limit",
        "access_policy": "login",
        "local_resource": "disk",
        "environment": "component",
        "template_or_extraction": "page_change",
    }.get(report.category.value, report.category.value)
    return UserFacingDiagnostic(
        legacy_category,
        report.cause,
        (message,),
        attempts,
        (report.action, "查看失败样本", "从最近检查点安全继续"),
        report.data_impact,
        report.retryable,
        report.help_id,
    )


def create_redacted_support_bundle(destination: Path, diagnostic: UserFacingDiagnostic, logs: tuple[str, ...]) -> Path:
    """Create a local support ZIP; secrets are removed before any file is written."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(redact_diagnostic_value(asdict(diagnostic)), ensure_ascii=False, indent=2)
    redacted_logs = "\n".join(redact_diagnostic_text(line) for line in logs)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostic.json", payload)
        archive.writestr("logs-redacted.txt", redacted_logs)
        archive.writestr("PRIVACY.txt", "凭据、Cookie、Token 和 API Key 已在本机脱敏；请在发送前复核。\n")
    return destination
