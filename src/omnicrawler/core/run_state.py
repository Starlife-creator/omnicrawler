from __future__ import annotations

RUN_STATES = frozenset(
    {
        "pending", "running", "succeeded", "failed", "paused",
        "cancelled", "retrying", "partial_success",
    }
)
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "cancelled", "partial_success"})
#: 真正交付了产出的终态：终态里去掉 `failed`。取消也会保留「已采到的结果」，
#: 所以「结果页能不能载入」与「任务是否成功」是两个判据，不要混用。
DELIVERED_RUN_STATES = TERMINAL_RUN_STATES - {"failed"}
#: 成功终态（含「部分成功」）：用于提示音、自动导出、托盘通知这类「算完成」的场合。
SUCCESS_RUN_STATES = frozenset({"succeeded", "partial_success"})
#: 完整成功（不含「部分成功」）：只有它才敢把「记录消失」判成删除 ——
#: 部分成功的运行里消失的记录也可能只是没采到。
COMPLETE_RUN_STATES = frozenset({"succeeded"})
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
