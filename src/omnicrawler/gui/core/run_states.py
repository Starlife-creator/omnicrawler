"""GUI 侧运行状态的**唯一表现层**：词表归一 + 文案 + 颜色 + 图标（W6.7 主项）。

## 背景（§5.3 #11「GUI 终态词表统一」）

同一个运行状态，此前在 GUI 里有**五份各写各的表**，而且**混用了两套词**：

* `gui/runner/worker_task_runner.py` 生产状态：后端返回的是规范词
  （`succeeded` / `failed` / `cancelled` / `partial_success`），却在这里被**压成** `finished` / `error`；
* `gui/delegates/run_controller.py`：一份「状态 → 文案」表 + 若干分支按 `finished` / `error` 判断；
* `gui/widgets/status_indicator.py`：又一份「状态 → 颜色」与「状态 → 文案」；
* `gui/views/task_history.py`：第三份（状态 → 图标）；
* `gui/home.py`：第四份「状态 → 文案」。

两套词 + 五份表的直接代价：**`partial_success` 被压成 `finished` 后，"部分成功"在界面上
与"完全成功"再也分不出来**（而这正是「存在错误记录」的提示依据）。

## 现在的约定

* **唯一真源**：`core/run_state.py`（`RUN_STATES` / `TERMINAL_RUN_STATES` / `STATUS_ALIASES`）；
* **生产**：一律发规范词（`succeeded` / `failed` / `cancelled` / `partial_success`）；
* **兼容读取**：`finished` / `error` 是 `STATUS_ALIASES` 里的别名 —— 旧历史记录读进来仍然可用；
* **表现**：文案/颜色/图标只在本模块定义一次，各 view 从这里取。
"""

from __future__ import annotations

from ...core.run_state import STATUS_ALIASES, TERMINAL_RUN_STATES, canonical_run_state
from ..i18n import _

#: GUI 专有的生命周期词（不属于核心运行状态机，但界面状态会用它）。
#: `idle` = 空闲；`stopping` = 正在安全停止；`starting` = 正在启动。
GUI_ONLY_STATES = frozenset({"idle", "stopping", "starting"})

#: 状态 → 中文文案。**只在这里定义一次**。
_STATE_LABELS: dict[str, str] = {
    "idle": _("空闲"),
    "pending": _("等待中"),
    "starting": _("启动中"),
    "running": _("运行中"),
    "paused": _("已暂停"),
    "stopping": _("正在安全停止"),
    "retrying": _("重试中"),
    "succeeded": _("已完成"),
    "partial_success": _("部分成功（存在错误记录）"),
    "failed": _("运行失败"),
    "cancelled": _("已取消"),
}

#: 状态 → 指示灯颜色令牌名（取 `VisualTokens` 上的字段）
_STATE_COLOR_TOKENS: dict[str, str] = {
    "idle": "indicator_idle",
    "pending": "indicator_idle",
    "starting": "indicator_running",
    "running": "indicator_running",
    "paused": "indicator_paused",
    "stopping": "indicator_paused",
    "retrying": "indicator_running",
    "succeeded": "indicator_finished",
    "partial_success": "indicator_warning",
    "failed": "indicator_error",
    "cancelled": "indicator_idle",
}

#: 状态 → 列表里的图标（历史记录等）
_STATE_ICONS: dict[str, str] = {
    "idle": "⏸️",
    "pending": "🕒",
    "retrying": "🔄",
    "running": "⏳",
    "paused": "⏸️",
    "stopping": "⏳",
    "succeeded": "✅",
    "partial_success": "⚠️",
    "failed": "❌",
    "cancelled": "🚫",
}


def normalize_state(value: str) -> str:
    """把任意来源的状态词归一：规范词原样、**别名按别名表**、GUI 生命周期词保留。

    ★ 本函数**永不抛错**：它跑在界面路径上，一个没见过的词不该让 UI 崩
    （未知值原样返回，界面按"原样显示"处理）。
    """
    text = str(value or "").strip()
    if not text:
        return "idle"
    lowered = text.casefold()
    if lowered in GUI_ONLY_STATES:
        return lowered
    try:
        return canonical_run_state(lowered)
    except ValueError:
        return lowered


def is_terminal(state: str) -> bool:
    """是否终态（核心的 `TERMINAL_RUN_STATES`，含别名归一）。"""
    return normalize_state(state) in TERMINAL_RUN_STATES


def state_label(state: str) -> str:
    """状态的中文文案（未知状态原样返回，便于排障时看到真实值）。"""
    normalized = normalize_state(state)
    return _STATE_LABELS.get(normalized, normalized)


def state_color_token(state: str) -> str:
    """状态对应的指示灯颜色令牌名（未知状态回落到 idle）。"""
    return _STATE_COLOR_TOKENS.get(normalize_state(state), "indicator_idle")


def state_icon(state: str) -> str:
    """状态对应的图标（未知状态用中性符号）。"""
    return _STATE_ICONS.get(normalize_state(state), "•")


def legacy_aliases() -> dict[str, str]:
    """暴露给测试/文档：别名表（`finished` → `succeeded`、`error` → `failed`）。"""
    return dict(STATUS_ALIASES)
