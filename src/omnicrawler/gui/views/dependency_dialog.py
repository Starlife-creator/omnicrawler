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


class _DependencyInstallWorker(BackgroundWorker):
    """后台执行多源回退安装（复用统一生命周期：成功/失败/取消三选一）。"""

    def __init__(
        self,
        requirement: str,
        *,
        registry: Any,
        python_executable: str = "",
        timeout_override: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._requirement = requirement
        self._registry = registry
        self._python_executable = python_executable
        self._timeout_override = timeout_override

    def work(self) -> dict[str, Any]:
        from ...services.dependency_installer import install_dependency

        sources: list[tuple[str, str]] = []
        if self._registry is not None:
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
        return {
            "ok": result.ok,
            "summary": result.summary,
            "detail": result.reason_chain(),
            "requirement": self._requirement,
        }


def prompt_and_install(
    parent: QWidget,
    items: Iterable[dict[str, Any]],
    *,
    registry: Any = None,
    on_finished: Callable[[], object] | None = None,
) -> bool:
    """就一组缺失依赖征询用户并安装。

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

        # 用户选择安装：走后台 worker（界面不冻结）
        outcome = {"ok": False}
        finished = {"done": False}

        def _on_succeeded(
            payload: Any,
            _requirement: str = requirement,
            _outcome: dict[str, Any] = outcome,
            _done: dict[str, bool] = finished,
        ) -> None:
            _outcome["ok"] = bool(payload.get("ok"))
            _done["done"] = True
            if payload.get("ok"):
                from ..widgets.toast import ToastManager

                ToastManager.instance().success(str(payload.get("summary") or _("安装成功")))
            else:
                _show_failure(parent, _requirement, str(payload.get("detail") or ""))

        worker = _DependencyInstallWorker(requirement, registry=registry, parent=parent)
        run_worker(worker, on_succeeded=_on_succeeded)
        # 等待本次安装结束（worker 是 QThread，这里用事件循环等待以免阻塞 UI 重绘）
        from PySide6.QtCore import QCoreApplication

        while not finished["done"] and worker.isRunning():
            QCoreApplication.processEvents()
        worker.wait(5000)
        if outcome["ok"]:
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
