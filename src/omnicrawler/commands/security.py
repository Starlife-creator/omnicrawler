from __future__ import annotations

from typing import Any

from ..core.config import load_config
from ..security.security_audit import egress_audit_report


def execute(config: str) -> dict[str, Any]:
    loaded = load_config(config)
    return egress_audit_report(loaded.workspace / "logs" / "egress-audit.jsonl")
