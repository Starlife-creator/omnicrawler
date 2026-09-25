"""依赖缺失对话框与安装编排（决策三 / 决策七 / 决策八 的 GUI 落点）。

职责单一：把预检产出的 ``action == "install"`` 检查项渲染成**可操作**的对话框，
并把用户确认的安装交给后台 worker（多源回退安装器）。这样"检测到缺失 → 提示 →
是否安装 → 装好 → 复检"闭环全在产品内完成，用户只做一次是/否判断。

两类依赖的处置差异（决策三）：
* ``dependency_class == "native"`` → **阻断**：不装不能跑，对话框只有「下载并安装」/「取消」；
* ``dependency_class == "plugin"`` → **不阻断**：插件声明的是依赖全集，本次可能用不到，
  对话框提供「安装」/「继续」两条路，用户自由选择。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget

from ..core.background_worker import BackgroundWorker, run_worker
from ..i18n import _

LOGGER = logging.getLogger(__name__)


def dependency_install_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    """从预检报告里挑出**可安装**的依赖检查项（原生 error + 插件 warning）。"""
    items: list[dict[str, Any]] = []
    for check in report.get("checks", []) or []:
        meta = check.get("fix") or {}
        if meta.get("action") != "install" or not meta.get("requirement"):
            continue
        if check.get("status") not in {"error", "warning"}:
            continue
        items.append(
            {
                "requirement": str(meta["requirement"]),
                "dependency_class": str(meta.get("dependency_class") or "native"),
                "title": str(check.get("title") or ""),
                "message": str(check.get("message") or ""),
                "blocking": check.get("status") == "error",
            }
        )
    return items


def install_result_payload(result: Any, requirement: str) -> dict[str, Any]:
    """把 ``InstallResult`` 压成可跨线程传递的普通 dict（含逐源归因）。

    把 ``attempts`` 一并带上，是为了让 GUI 侧能**不 import 安装器**就判断
    "是否该建议启用镜像加速"（谓词只读 ``host`` / ``kind``）。
    """
    attempts = [
        {
            "host": str(getattr(attempt, "host", "") or ""),
            "canonical": str(getattr(attempt, "canonical", "") or ""),
            "kind": str(getattr(attempt, "kind", "") or ""),
            "ok": bool(getattr(attempt, "ok", False)),
        }
        for attempt in (getattr(result, "attempts", None) or ())
    ]
    return {
        "ok": bool(result.ok),
        "summary": result.summary,
        "detail": result.reason_chain(),
        "requirement": requirement,
        "attempts": attempts,
    }


def should_offer_mirror_from_payload(
    payload: dict[str, Any], *, config_raw: dict[str, Any] | None
) -> bool:
    """从 worker 回传的 payload 判断"该不该弹一次启用镜像的提示"。

    复用纯函数 ``mirror_presets.should_offer_mirror_acceleration``，把 payload 里
    的 ``ok`` / ``attempts`` 还原成它需要的鸭子类型属性。``config_raw`` 为空时
    视为"未启用镜像"（GUI 尚未绑定配置路径的兜底）。
    """
    from ...services.mirror_presets import should_offer_mirror_acceleration

    if not payload or payload.get("ok"):
        return False

    class _Attempt:
        __slots__ = ("host", "kind")

        def __init__(self, item: dict[str, Any]) -> None:
            self.host = str(item.get("host") or "")
            self.kind = str(item.get("kind") or "")

    class _Result:
        __slots__ = ("ok", "attempts")

        def __init__(self, items: list[dict[str, Any]]) -> None:
            self.ok = bool(payload.get("ok"))
            self.attempts = [_Attempt(item) for item in items]

    return should_offer_mirror_acceleration(
        _Result(list(payload.get("attempts") or [])),
        config_raw=config_raw or {},
    )


def mirror_retry_sources(config_raw: dict[str, Any]) -> list[tuple[str, str]]:
    """启用镜像后**同一轮内**重试用的源列表（官方在首位，其余按预置顺序）。"""
    from ...services.mirror_presets import mirror_endpoints_from_patch

    try:
        return mirror_endpoints_from_patch(config_raw or {})
    except Exception as exc:  # noqa: BLE001 - 兜底直连官方，绝不因镜像清单异常卡死安装
        LOGGER.warning("解析镜像清单失败，回退官方源: %s", exc)
        return []


class _DependencyInstallWorker(BackgroundWorker):
    """后台执行多源回退安装（复用统一生命周期：成功/失败/取消三选一）。"""

    def __init__(
        self,
        requirement: str,
        *,
        registry: Any,
        python_executable: str = "",
        timeout_override: float | None = None,
        sources_override: Iterable[tuple[str, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._requirement = requirement
        self._registry = registry
        self._python_executable = python_executable
        self._timeout_override = timeout_override
        self._sources_override = list(sources_override) if sources_override is not None else None

    def work(self) -> dict[str, Any]:
        from ...services.dependency_installer import install_dependency

        sources: list[tuple[str, str]] = []
        if self._sources_override is not None:
            # 「启用镜像加速」后的重试：用启用后的源列表（官方仍首位），
            # 不再读 registry（此刻 registry 尚未按新配置重建）。
            sources = list(self._sources_override)
        elif self._registry is not None:
            try:
                sources = self._registry.ordered_endpoints("pypi.org")
            except Exception as exc:  # noqa: BLE001 - 镜像不可用不影响直连官方
                LOGGER.warning("读取镜像组失败，回退官方源: %s", exc)
        result = install_dependency(
            self._requirement,
            sources=sources,
            registry=self._registry,
            python_executable=self._python_executable,
            timeout_override=self._timeout_override,
        )
        return install_result_payload(result, self._requirement)


def _run_install_attempt(
    parent: QWidget,
    requirement: str,
    *,
    registry: Any,
    sources_override: Iterable[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """同步跑一次安装（后台 worker + 事件循环等待），返回回传 payload。

    同步等待是刻意的：调用方处在"是否继续运行"的决策点，必须拿到**确定结论**
    才能决定阻断/放行；等待期间用 ``processEvents`` 保持界面重绘。
    """
    outcome: dict[str, Any] = {"payload": None}

    def _on_succeeded(payload: Any, _outcome: dict[str, Any] = outcome) -> None:
        _outcome["payload"] = payload

    worker = _DependencyInstallWorker(
        requirement,
        registry=registry,
        sources_override=sources_override,
        parent=parent,
    )
    run_worker(worker, on_succeeded=_on_succeeded)
    from PySide6.QtCore import QCoreApplication

    while outcome["payload"] is None and worker.isRunning():
        QCoreApplication.processEvents()
    worker.wait(5000)
    return outcome["payload"] or {"ok": False, "detail": "", "requirement": requirement}


def install_with_mirror_offer(
    parent: QWidget,
    requirement: str,
    *,
    registry: Any = None,
    config_raw: dict[str, Any] | None = None,
    persist_patch: Callable[[dict[str, Any]], object] | None = None,
) -> tuple[bool, bool]:
    """安装一个依赖；**当且仅当真实安装失败且轮到镜像源时**弹一次启用镜像提示。

    触发点（用户要求：接线在"真实安装失败、该轮到镜像源"的时刻）：

    1. 先按当前配置（无镜像 ⇒ 官方直连）安装一次；
    2. 若失败，且失败归因里出现**官方源的网络类失败**、且**当前未启用镜像**，
       才弹一次「官方源连接失败，是否启用国内镜像加速？」——这正是"官方源挂了、
       接下来该轮到镜像源"的那一步；
    3. 用户点「启用并重试」⇒ 把预置镜像清单落盘（``persist_patch``）+ **同一轮内**
       用镜像源重试（官方仍在首位，健康时不会被绕过）；
    4. 用户点「不启用」⇒ 保留失败原因链展示，不重试。

    只弹**一次**：失败归因里已含镜像 host 的失败时不再重复问（用户已选择过）。

    Returns
    -------
    tuple[bool, bool]
        ``(ok, enabled_mirrors)`` —— 是否装成功、本轮是否启用了镜像。
    """
    payload = _run_install_attempt(parent, requirement, registry=registry)
    if payload.get("ok"):
        return True, False

    if not should_offer_mirror_from_payload(payload, config_raw=config_raw):
        _show_failure(parent, requirement, str(payload.get("detail") or ""))
        return False, False

    box = QMessageBox(parent)
    box.setWindowTitle(_("官方源连接失败"))
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(_("无法从官方源安装 {0}").format(requirement))
    box.setInformativeText(
        _(
            "官方 PyPI 源连接失败。是否启用国内镜像加速后重试？\n"
            "启用后会写入配置的 mirrors 节，后续安装将优先使用国内镜像。"
        )
    )
    enable_btn = box.addButton(_("启用并重试"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(_("不启用"), QMessageBox.ButtonRole.RejectRole)
    box.setDetailedText(str(payload.get("detail") or ""))
    box.exec()
    if box.clickedButton() is not enable_btn:
        return False, False

    # 落盘补丁（失败不阻断重试：即便保存失败，本轮仍用镜像源试一次）
    patch_applied = False
    if config_raw is not None:
        from ...services.mirror_presets import enable_mirrors_patch

        patch = enable_mirrors_patch(config_raw)
        # 就地同步到调用方的 config_raw：同一批多依赖时，后续项据此判定
        # "镜像已启用"从而**不再重复弹框**（只弹一次）。
        config_raw["mirrors"] = patch["mirrors"]
        if persist_patch is not None:
            try:
                persist_patch(patch)
                patch_applied = True
            except Exception as exc:  # noqa: BLE001 - 保存失败不阻断本轮镜像重试
                LOGGER.warning("镜像配置保存失败: %s", exc)

    retry_payload = _run_install_attempt(
        parent,
        requirement,
        registry=registry,
        sources_override=mirror_retry_sources(config_raw or {}),
    )
    if retry_payload.get("ok"):
        from ..widgets.toast import ToastManager

        ToastManager.instance().success(
            str(retry_payload.get("summary") or _("已通过镜像源安装成功"))
        )
        return True, patch_applied
    _show_failure(parent, requirement, str(retry_payload.get("detail") or ""))
    return False, patch_applied


def prompt_and_install(
    parent: QWidget,
    items: Iterable[dict[str, Any]],
    *,
    registry: Any = None,
    config_raw: dict[str, Any] | None = None,
    persist_patch: Callable[[dict[str, Any]], object] | None = None,
    on_finished: Callable[[], object] | None = None,
) -> bool:
    """就一组缺失依赖征询用户并安装。

    Parameters
    ----------
    config_raw:
        当前配置字典（读 ``mirrors`` 节判断"是否已启用镜像"）。为空 ⇒ 视为未启用。
    persist_patch:
        保存"启用镜像"补丁的回调（由调用方注入落盘路径，本模块不碰文件系统）。

    Returns
    -------
    bool
        ``True`` 表示用户选择了安装（或全部已尝试）；``False`` 表示用户取消。
        调用方据此决定：原生依赖（阻断类）未装 ⇒ 中止运行；插件依赖（非阻断类）
        未装 ⇒ 照常运行。
    """
    pending: list[dict[str, Any]] = [item for item in items if item.get("requirement")]
    if not pending:
        return True

    blocking = [item for item in pending if item.get("blocking")]
    installed_any = False

    for item in pending:
        requirement = str(item["requirement"])
        is_pending_blocking = bool(item.get("blocking"))
        box = QMessageBox(parent)
        box.setWindowTitle(_("缺少依赖"))
        box.setIcon(QMessageBox.Icon.Critical if is_pending_blocking else QMessageBox.Icon.Information)
        if is_pending_blocking:
            box.setText(
                _("运行所需依赖未安装：{0}").format(requirement)
            )
            box.setInformativeText(
                _("不安装将无法运行。是否现在自动下载并安装？")
            )
            install_btn = box.addButton(_("下载并安装"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(_("取消"), QMessageBox.ButtonRole.RejectRole)
        else:
            box.setText(_("插件声明的依赖未安装：{0}").format(requirement))
            box.setInformativeText(
                _("这是插件声明的依赖全集，本次运行可能用不到。可自选是否安装，不装也可以继续。")
            )
            install_btn = box.addButton(_("安装"), QMessageBox.ButtonRole.ActionRole)
            box.addButton(_("继续运行"), QMessageBox.ButtonRole.RejectRole)
        box.setDetailedText(str(item.get("message") or ""))
        box.exec()
        if box.clickedButton() is not install_btn:
            if is_pending_blocking:
                # 原生依赖是阻断类：用户拒绝 ⇒ 整组中止
                return False
            continue

        # 用户选择安装：走统一的 安装 → （失败时）镜像提示 → 重试 流程
        ok, _enabled = install_with_mirror_offer(
            parent,
            requirement,
            registry=registry,
            config_raw=config_raw,
            persist_patch=persist_patch,
        )
        if ok:
            installed_any = True
        elif is_pending_blocking:
            return False

    if on_finished is not None:
        on_finished()
    # 阻断类全部装好（或本就无阻断项）才返回 True
    return not blocking or installed_any or all(
        not item.get("blocking") for item in pending
    )


def _show_failure(parent: QWidget, requirement: str, detail: str) -> None:
    """安装失败：Toast 一句结论 + 持久对话框给完整原因链（可复制）。"""
    from ..widgets.toast import ToastManager

    ToastManager.instance().error(_("依赖安装失败：") + requirement)
    box = QMessageBox(QMessageBox.Icon.Critical, _("依赖安装失败"), _("无法安装 {0}").format(requirement), parent=parent)
    box.setDetailedText(detail or _("所有来源均不可用。"))
    box.exec()
