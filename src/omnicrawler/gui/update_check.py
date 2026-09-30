"""GUI「检查更新」入口：后台线程调用自更新 CLI 逻辑，结果以对话框/Toast 呈现。

设计要点：
- **不占 UI 线程**：检查走 QThread（沿用 home.py 的 BackgroundWorker 模式与 S1.1.5 释放约定）；
- **无配置不检查**：MainWindow 未加载任务配置时直接 Toast 提示（检查需要 config 作 Egress 判据）；
- **体积对比**（照 B 站弹窗形态）：增量与全量并排 —— 每版都同时发布两者（统一发布形态）；
- 执行更新仍走 CLI（``omnicrawler self-update apply --yes``），本入口只做检查与说明。
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QThread, Signal

from .i18n import _

logger = logging.getLogger(__name__)


#: 建议用户执行的更新命令（执行仍在 CLI 侧，含 --yes 判据）
_APPLY_HINT = "omnicrawler self-update apply --yes"


def _human_bytes(size: int) -> str:
    """与 commands.self_update._human_bytes 同口径（那里不 import GUI，这里独立实现）。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def format_update_summary(payload: dict[str, Any]) -> str:
    """把 check 的产出整理成给用户看的摘要（B 站弹窗形态：增量/全量并排）。"""
    lines: list[str] = [_("当前版本：{0}").format(payload.get("current_version", "?"))]
    latest = payload.get("latest_version")
    status = str(payload.get("status", ""))
    if status == "up-to-date":
        lines.append(_("已是最新版本，无需更新。"))
        return "\n".join(lines)
    if latest:
        lines.append(_("最新版本：{0}").format(latest))

    options = payload.get("options") or {}
    incremental = options.get("incremental") or {}
    full = options.get("full") or {}
    if incremental.get("available"):
        lines.append(
            _("增量更新（约 {0}）").format(_human_bytes(int(incremental.get("size", 0))))
        )
    if full.get("available"):
        size = _human_bytes(int(full.get("size", 0)))
        lines.append(_("全量包（约 {0}）").format(size))
    plan = payload.get("payload_plan")
    if isinstance(plan, dict) and plan.get("needs_download") is not None:
        lines.append(
            _("按逐文件清单比对：本机需更新 {0}/{1} 个文件").format(
                plan.get("needs_download"), plan.get("files_total")
            )
        )

    notes = payload.get("notes")
    if notes:
        lines.append("")
        lines.append(str(notes))
    lines.append("")
    lines.append(_("执行更新（命令行）：{0}").format(_APPLY_HINT))
    return "\n".join(lines)


class _CheckWorker(QThread):
    """后台线程：调用自更新 CLI 的 check（沿用 home.py 的释放约定 S1.1.5）。"""

    finished_with = Signal(dict, int)

    def __init__(self, config_path: str, parent: Any = None) -> None:
        super().__init__(parent)
        self._config_path = config_path

    def run(self) -> None:
        from ..commands import self_update as cmd_self_update
        from ..commands.self_update import EXIT_FAILED

        try:
            payload, code = cmd_self_update.check(config_path=self._config_path)
        except Exception as exc:  # noqa: BLE001 - 异常需上抛给 UI（C12/C25）
            logger.debug("update check failed: %s", exc, exc_info=True)
            self.finished_with.emit(
                {"status": "failed", "detail": str(exc)}, EXIT_FAILED
            )
            return
        self.finished_with.emit(payload, code)
        self.deleteLater()


def _f(text: str) -> str:
    """本地占位：i18n 的 f-string 包装（保持与 `_()` 相同的调用形）。"""
    return text
