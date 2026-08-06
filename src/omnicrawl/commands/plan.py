from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..pipeline_ops.plan_compiler import _redact_for_hash
from ..security.security_audit import scan_config_text
from ..services.application_service import ApplicationService


def execute(config: str, *, compare: str = "", output: str = "") -> dict[str, Any]:
    service = ApplicationService(config)
    result = service.diff(compare) if compare else service.compile()
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        # S2.2.4：导出前脱敏（按敏感键名掩码）+ 明文凭据扫描复核，命中即拒绝
        redacted = _redact_for_hash(result)
        import yaml

        text = yaml.safe_dump(redacted, allow_unicode=True, sort_keys=False)
        report = scan_config_text(text)
        if not report["ok"]:
            lines = "、".join(str(item["line"]) for item in report["findings"])
            raise ValueError(
                f"plan 输出包含 {len(report['findings'])} 处未脱敏明文凭据（第 {lines} 行），"
                "已拒绝写入；请改用 secret:// 引用或环境变量。"
            )
        target.write_text(json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8")
        result["output"] = str(target)
    return result
