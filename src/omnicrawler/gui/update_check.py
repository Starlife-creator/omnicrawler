"""GUI「检查更新」入口：后台线程调用自更新逻辑，结果以对话框/Toast 呈现。

设计要点：
- **不占 UI 线程**：检查与**执行**都走 QThread（沿用 home.py 的 BackgroundWorker 模式与
  S1.1.5 释放约定）；
- **无配置不检查**：MainWindow 未加载任务配置时直接 Toast 提示（检查需要 config 作 Egress 判据）；
- **体积对比**（照 B 站弹窗形态）：增量与全量并排 —— 每版都同时发布两者（统一发布形态）；
- ★ **执行也在应用内**：`self-update apply` 是**破坏性动作**（覆盖应用文件），所以 GUI 侧必须
  走**同一套编排**（`commands.self_update.apply`）并显式确认，**不另写一份落地逻辑** ——
  判据与 CLI 完全一致（验签、逐文件哈希、受保护路径、缺成员自愈、`--yes` 语义＝这里的确认框）。
  此前只给一句"请到命令行执行"，等于让 GUI 用户拿不到已经可用的能力。
- ★ **本平台没有自动落地能力时不给动作**（macOS）：只给"下哪个文件、多大"
  （见 `options.manual_install` 与 `services/update_feed.AUTO_APPLY_PLATFORMS`）。
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from PySide6.QtCore import QThread, Signal

from .i18n import _

logger = logging.getLogger(__name__)


class ApplySwitches(TypedDict, total=False):
    """`commands.self_update.apply` 的落地方式开关（键名必须与那个函数的参数名逐字一致）。"""

    full: bool
    to_versions: bool


#: 三种落地方式的标识（与 `commands.self_update.apply` 的开关一一对应）。
MODE_INCREMENTAL = "incremental"
MODE_FULL = "full"
MODE_VERSIONS = "versions"


def _human_bytes(size: int) -> str:
    """与 commands.self_update._human_bytes 同口径（那里不 import GUI，这里独立实现）。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def available_apply_modes(options: dict[str, Any]) -> list[str]:
    """**纯函数**：本轮可选哪几种落地方式（对话框与用例共用同一判据，不各写一套）。

    顺序＝推荐顺序：增量（最省）→ 全量就地（一份磁盘）→ 装到 `versions/`（两份，可回退）。

    `manual_install` / `auto_apply=False` ⇒ **返回空列表**：调用方据此不提供任何动作，
    只给下载信息。"给了按钮但点了必然失败"比"没有按钮"更糟。

    ★ 只有**显式**的 false 才拦：字段缺席时按"可更新"处理 —— 与清单侧 `auto_apply`
    缺省 true 同一口径。（早先写成 `not options.get("auto_apply")`，把"字段缺席"也当成
    "不能更新"，于是老载荷/精简载荷会静默失去更新按钮。）
    """
    if options.get("manual_install") or options.get("auto_apply") is False:
        return []
    modes: list[str] = []
    if (options.get("incremental") or {}).get("available"):
        modes.append(MODE_INCREMENTAL)
    if (options.get("full") or {}).get("available"):
        modes.append(MODE_FULL)
        if options.get("install_to_versions"):
            modes.append(MODE_VERSIONS)
    return modes


def describe_apply_mode(mode: str, options: dict[str, Any]) -> str:
    """把落地方式说成人话（含体积与磁盘代价 —— 用户要据此做选择）。"""
    incremental = options.get("incremental") or {}
    full = options.get("full") or {}
    if mode == MODE_INCREMENTAL:
        return _("增量更新（约 {0}，只下变化的那部分）").format(
            _human_bytes(int(incremental.get("size", 0)))
        )
    if mode == MODE_VERSIONS:
        return _("全量·装到 versions/（约 {0}，占两份磁盘，可随时回退旧版）").format(
            _human_bytes(int(full.get("size", 0)))
        )
    return _("全量·就地替换（约 {0}，占一份磁盘）").format(
        _human_bytes(int(full.get("size", 0)))
    )


def apply_kwargs_for_mode(mode: str) -> ApplySwitches:
    """落地方式 ⇒ `commands.self_update.apply` 的开关（**唯一映射**，避免两处各写一套）。

    ★ 返回 `TypedDict` 而不是裸 `dict[str, bool]`：worker 用 `**` 展开时 mypy 才能逐个
    核对参数名与类型；裸 dict 会让它只能报"incompatible type"（本项目把 mypy 当门禁）。
    """
    return {
        MODE_INCREMENTAL: ApplySwitches(),
        MODE_FULL: ApplySwitches(full=True),
        MODE_VERSIONS: ApplySwitches(full=True, to_versions=True),
    }[mode]


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
    if options.get("manual_install"):
        # ★ 如实说"只能手动装"（本平台自动更新做不到，见 services/update_feed 的能力表）。
        #   不给出一个点了必然失败的动作 —— 只给下载信息。
        lines.append(_("★ 本平台不支持自动更新，需要手动下载并替换："))
        if full.get("available"):
            lines.append(
                _("下载包：{0}（约 {1}）").format(
                    full.get("name", ""), _human_bytes(int(full.get("size", 0)))
                )
            )
        lines.append(_("请到本版本的发布下载页取上表文件，解压后替换原目录。"))
        notes = payload.get("notes")
        if notes:
            lines.append("")
            lines.append(_("更新说明："))
            lines.append(str(notes))
        return "\n".join(lines)
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
    if available_apply_modes(options):
        lines.append("")
        lines.append(_("可直接在应用内更新（更新后需要重启应用才会生效）。"))
    return "\n".join(lines)


def format_apply_result(payload: dict[str, Any], code: int) -> str:
    """把 `apply` 的产出整理成给用户看的结论（成功要给"下一步"，失败要给原因）。"""
    status = str(payload.get("status", ""))
    if status != "applied":
        detail = str(payload.get("detail") or payload.get("status") or _("未知原因"))
        return _("更新未能完成：{0}").format(detail)
    lines: list[str] = [_("更新已应用（{0}）。").format(payload.get("staged_version", "?"))]
    applied = payload.get("applied_files")
    if applied is not None:
        lines.append(_("已替换 {0} 个文件。").format(applied))
    plan = payload.get("plan") or {}
    if plan.get("mode"):
        lines.append(_("方式：{0}").format(plan.get("mode")))
    if plan.get("retired_entries"):
        lines.append(_("旧入口已改名 .outdated（防止误点旧版）。"))
    pending = payload.get("pending_cleanup") or []
    if pending:
        lines.append(
            _("{0} 个旧文件正被占用，已登记到下次启动时清理。").format(len(pending))
        )
    fallback = plan.get("delta_fallback")
    if isinstance(fallback, dict):
        lines.append(
            _("★ 本次改用了全量包（{0} 个文件不在变更包里，多为你自己改动或上次更新中断）。").format(
                fallback.get("missing_count", 0)
            )
        )
    lines.append("")
    lines.append(_("请重启应用以运行新版本。"))
    return "\n".join(lines)


class _CheckWorker(QThread):
    """后台线程：调用自更新的 check（沿用 home.py 的释放约定 S1.1.5）。"""

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


class _ApplyWorker(QThread):
    """后台线程：就地执行更新（**复用 commands 层编排**，不另写落地逻辑）。

    ★ 为什么在进程内调 `commands.self_update.apply` 而不是去 spawn CLI：
    ① GUI 与 CLI 必须共用**同一套判据**（验签、逐文件哈希、受保护路径、缺成员自愈）；
    ② 冻结包环境下"找得到 CLI"本身是个易错前提（`bundled_cli_path` 那段历史就是为此）；
    ③ 就地调用拿得到**结构化结果**（plan/mode/pending_cleanup），可以直接呈现给用户。

    ★ 破坏性动作的确认判据由调用方承担：GUI 的确认对话框 ＝ CLI 的 `--yes`
    （`core.safe_action` 的同一套语义，只是载体不同）。
    """

    finished_with = Signal(dict, int)

    def __init__(self, config_path: str, mode: str, parent: Any = None) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self._mode = mode

    def run(self) -> None:
        from ..commands import self_update as cmd_self_update
        from ..commands.self_update import EXIT_FAILED

        try:
            payload, code = cmd_self_update.apply(
                config_path=self._config_path,
                **apply_kwargs_for_mode(self._mode),
            )
        except Exception as exc:  # noqa: BLE001 - 异常需上抛给 UI（C12/C25）
            logger.debug("update apply failed: %s", exc, exc_info=True)
            self.finished_with.emit({"status": "failed", "detail": str(exc)}, EXIT_FAILED)
            return
        self.finished_with.emit(payload, code)
        self.deleteLater()


def _f(text: str) -> str:
    """本地占位：i18n 的 f-string 包装（保持与 `_()` 相同的调用形）。"""
    return text
