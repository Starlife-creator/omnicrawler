"""Host-owned evaluation of advisory Contract 2 hook results.

Plugins may describe intent, but they never directly skip a request or inject
conditional headers.  The pipeline validates all proposals and obtains cache
validators from its own StateStore.
"""

from __future__ import annotations

from typing import Any


def choose_fetch_advice(results: list[Any]) -> dict[str, str] | None:
    """Return one unambiguous, bounded before-fetch proposal."""

    proposals: list[dict[str, str]] = []
    for result in results[:16]:
        if not isinstance(result, dict):
            continue
        raw = result.get("fetch_advice")
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action", "")).strip().casefold()
        if action not in {"conditional_revalidate", "force_fetch"}:
            continue
        reason = str(raw.get("reason", "")).strip()[:240]
        proposals.append({"action": action, "reason": reason})
    actions = {proposal["action"] for proposal in proposals}
    if len(actions) != 1:
        return None
    return proposals[0]
