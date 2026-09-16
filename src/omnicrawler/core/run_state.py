from __future__ import annotations

RUN_STATES = frozenset(
    {
        "pending", "running", "succeeded", "failed", "paused",
        "cancelled", "retrying", "partial_success",
    }
)
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "cancelled", "partial_success"})
STATUS_ALIASES = {
    "completed": "succeeded",
    "completed_with_errors": "partial_success",
    "stopped": "cancelled",
    "interrupted": "cancelled",
    "resource_limited": "failed",
    # ★ W6.7（2026-09-15）：GUI 早就用过这两个词，且它们可能写在**历史记录 / 会话文件**里。
    # 统一词表时把它们登记为**别名**（兼容读取），而不是让 `canonical_run_state` 抛错 ——
    # 旧数据读进来必须仍然可用。
    "finished": "succeeded",
    "error": "failed",
}
ALLOWED_TRANSITIONS = {
    "pending": frozenset({"running", "cancelled"}),
    "running": frozenset(
        {"succeeded", "failed", "paused", "cancelled", "retrying", "partial_success"}
    ),
    "paused": frozenset({"running", "failed", "cancelled"}),
    "retrying": frozenset({"running", "failed", "cancelled"}),
    "failed": frozenset({"retrying"}),
    "succeeded": frozenset(),
    "cancelled": frozenset(),
    "partial_success": frozenset(),
}


def canonical_run_state(value: str) -> str:
    state = STATUS_ALIASES.get(value.casefold(), value.casefold())
    if state not in RUN_STATES:
        raise ValueError(f"未知任务状态: {value}")
    return state


def require_transition(current: str, target: str) -> tuple[str, str]:
    current = canonical_run_state(current)
    target = canonical_run_state(target)
    if current == target:
        return current, target
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"非法任务状态转换: {current} -> {target}")
    return current, target
