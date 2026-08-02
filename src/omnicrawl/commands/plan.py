from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..services.application_service import ApplicationService


def execute(config: str, *, compare: str = "", output: str = "") -> dict[str, Any]:
    service = ApplicationService(config)
    result = service.diff(compare) if compare else service.compile()
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["output"] = str(target)
    return result
