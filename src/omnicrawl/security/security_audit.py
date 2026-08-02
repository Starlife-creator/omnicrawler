from __future__ import annotations

import json
import re
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|cookie|api[-_]?key|client[-_]?secret)\s*:\s*([^\n#]+)"
)


def scan_config_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ok": True, "findings": []}
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        match = _KEY_PATTERN.search(line)
        if not match:
            continue
        value = match.group(2).strip().strip("'\"")
        if not value or value.startswith(("secret://", "${", "<redacted")):
            continue
        findings.append(
            {
                "line": line_number,
                "key": match.group(1),
                "severity": "error",
                "message": "检测到可能的明文凭据；请改用 secret:// 引用或环境变量。",
            }
        )
    return {"ok": not findings, "findings": findings}


def pii_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    patterns = {
        "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
        "phone": re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
        "id_card_candidate": re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    }
    counts = {name: 0 for name in patterns}
    for record in records:
        text = " ".join(str(value) for value in record.values() if value is not None)
        for name, pattern in patterns.items():
            counts[name] += len(pattern.findall(text))
    return counts


def egress_audit_report(path: Path) -> dict[str, Any]:
    """Summarise the redacted, append-only task network boundary log."""

    events: Counter[str] = Counter()
    purposes: Counter[str] = Counter()
    subjects: Counter[str] = Counter()
    boundaries: Counter[tuple[str, str, int]] = Counter()
    sdk_boundaries: Counter[str] = Counter()
    invalid_lines = 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if not isinstance(item, dict):
                invalid_lines += 1
                continue
            event = str(item.get("event", "unknown"))
            events[event] += 1
            purpose = str(item.get("purpose", ""))
            subject = str(item.get("subject", ""))
            if purpose:
                purposes[purpose] += 1
            if subject:
                subjects[subject] += 1
            parts = urllib.parse.urlsplit(str(item.get("url", "")))
            if parts.hostname:
                port = parts.port or (443 if parts.scheme in {"https", "wss"} else 80)
                boundaries[(parts.scheme, parts.hostname.casefold(), port)] += 1
            if event == "sdk_transport_boundary":
                sdk_boundaries[str(item.get("transport", "unknown"))] += 1
    return {
        "ok": invalid_lines == 0,
        "audit_file": str(path),
        "event_count": sum(events.values()),
        "events": dict(sorted(events.items())),
        "purposes": dict(sorted(purposes.items())),
        "subjects": dict(sorted(subjects.items())),
        "accessed_boundaries": [
            {"scheme": scheme, "host": host, "port": port, "events": count}
            for (scheme, host, port), count in sorted(boundaries.items())
        ],
        "sdk_transport_exceptions": dict(sorted(sdk_boundaries.items())),
        "blocked_attempts": events["blocked"],
        "invalid_lines": invalid_lines,
    }
