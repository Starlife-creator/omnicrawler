"""`surface.foreground.*` —— 宿主拥有的**前景层**（overlay）能力（2026-11-28）。

## 与 `surface.background.*` 的关系

两者是**对称**的两层，方向相反：

| | `surface.background.*` | `surface.foreground.*`（本模块）|
|---|---|---|
| 位置 | 主窗口**底下**（`layer.lower()`）| 中央区**之上**（`overlay.raise_()`）|
| 用途 | 氛围底图 / 媒体 / 宿主渲染帧 | 插件自己的浮层（提示、角标、进度遮罩）|
| 输入 | 完全穿透（`WA_TransparentForMouseEvents`）| **默认穿透**，可显式请求交互 |

## 硬规则（不是约定，是代码保证）

1. **从结构上盖不住菜单 / 对话框 / 停靠面板**：
   overlay 的 parent 是 `centralWidget()`，而 `QMenuBar` / `QDockWidget` /
   对话框都不在 `centralWidget` 里 ⇒ `raise_()` 在 `centralWidget` 的兄弟层级内
   排序，**不可能**盖到它们上面。这条不依赖任何运行时检查。
2. **同一时刻只有一个活跃前景层**：切换 scope 时上一个被 `hide()`，
   而不是叠着 —— 叠层会让「谁在响应点击」变得不可推断。
3. **减少动画时暂停**：`reduced_motion` 或高对比模式下，任何动画/旋转立即暂停。
4. **卸载时清理**：控制器 `close()` 必须 `hide()` + `deleteLater()`，
   否则插件卸载后浮层留在屏幕上（那是"关不掉的幽灵"）。

## 为什么没有 `hotspots`

曾考虑让插件声明「这块矩形可点」。**否决**：

- 坐标必须从 overlay 反算回宿主语义，插件窗口缩放/DPI 变化时换算必然出错；
- 可点区域无法被 Tab 遍历 ⇒ 键盘用户完全够不到，违反本项目无障碍验收；
- 真正的交互入口是 `view.action`（声明式按钮），已经有成熟通道。

浮层只做**展示**。要交互就用声明式组件。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from ..plugins.plugin_declarative import (
    FOREGROUND_CONTENT_KINDS as _DECLARATIVE_CONTENT_KINDS,
)
from ..plugins.plugin_declarative import (
    FOREGROUND_SCOPES as _DECLARATIVE_SCOPES,
)
from ..plugins.plugin_declarative import (
    FOREGROUND_Z_LEVELS as _DECLARATIVE_Z_LEVELS,
)
from .i18n import _

LOGGER = logging.getLogger(__name__)

#: PNG 文件头。前景层只接受 PNG 帧（宿主渲染输出的唯一形态）。
#:
#: 放在模块级而不是函数里：`N806`（函数内非常量名）会判红，而这不是噪声 ——
#: 模块级常量确实更符合"这是协议事实"的语义。
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: 词表的**唯一真源**在 `plugins/plugin_declarative.py`（纯数据、无 Qt），
#: 因为它是**协议事实**而不是渲染细节 —— 契约校验层必须能引用它。
#:
#: 这里只做 re-export，方便 GUI 侧照旧 `from .foreground_surface import ...`；
#: **不要再在本文件里写一份字面量**（两份迟早不一致，而这类漂移不报错）。
FOREGROUND_Z_LEVELS = _DECLARATIVE_Z_LEVELS
FOREGROUND_SCOPES = _DECLARATIVE_SCOPES
FOREGROUND_CONTENT_KINDS = _DECLARATIVE_CONTENT_KINDS


@dataclass(frozen=True, slots=True)
class ForegroundRegistration:
    """一个插件声明的前景层（内部用，不暴露给插件）。"""

    surface_id: str
    label: str


class _ForegroundLayer(QtWidgets.QWidget):
    """宿主绘制的前景层：只画「已渲染 PNG」或「纯色 + 透明度」。"""

    def __init__(self, owner: QtWidgets.QWidget) -> None:
        super().__init__(owner)
        self.setObjectName("pluginForegroundLayer")
        # 浮层是纯展示面、不接收输入 ⇒ 无障碍语义是「装饰」。
        # 仍必须给名字：没有名字的 QWidget 会让读屏器把它念成未命名控件。
        self.setAccessibleName(_("插件浮层（纯展示，不接收输入）"))
        self.setAccessibleDescription(_("由插件请求显示，宿主绘制；不响应鼠标与键盘"))
        self.pixmap: QtGui.QPixmap | None = None
        self.solid: QtGui.QColor | None = None
        self.opacity = 1.0
        # 默认穿透：浮层是展示面，不该抢输入。要交互请用声明式组件。
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt 命名
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setOpacity(self.opacity)
        if self.solid is not None:
            painter.fillRect(self.rect(), self.solid)
        if self.pixmap is not None and not self.pixmap.isNull():
            scaled = self.pixmap.scaled(
                self.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            point = QtCore.QPoint(
                (self.width() - scaled.width()) // 2,
                (self.height() - scaled.height()) // 2,
            )
            painter.drawPixmap(point, scaled)

class ForegroundController(QtCore.QObject):
    """宿主拥有的前景层控制器：一个插件一个，生命周期与插件面板绑定。

    ★ 为什么不复用 `BackgroundController`：后者的职责是「把背景铺满并沉下去」
      （`layer.lower()`、媒体轮播、面板不透明度…），语义完全相反。
      共用一个类会让每个方法都要 `if role == ...`，那是伪复用。

      共享的是**约束**（z 档位、scope、reduced_motion、清理），不是实现。
    """

    def __init__(self, main_window: Any, registration: ForegroundRegistration) -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.registration = registration
        # ★ 结构保证的落点：parent 是 centralWidget，**不是** main_window。
        #   于是 raise_() 只在中央区内排序，永远盖不到菜单 / 停靠面板 / 对话框。
        central = main_window.centralWidget()
        # ★ 没有 centralWidget ⇒ **不创建图层**，能力明确报不可用（fail-closed）。
        #
        #   不退回 `main_window` 当父级：那会让 `raise_()` 把浮层抬到停靠面板与
        #   菜单之上 —— 正是本模块要禁止的那件事。宁可没有前景层，也不要一个
        #   能盖住外壳的浮层。
        self.layer: _ForegroundLayer | None = None
        if central is not None:
            self.layer = _ForegroundLayer(central)
            self.layer.setObjectName(f"pluginForeground_{registration.surface_id}")
        else:  # pragma: no cover - 宿主装配异常（仅测试里的假窗口）
            LOGGER.warning(
                _("宿主主窗口没有 centralWidget，前景层不可用（surface_id=%s）"),
                registration.surface_id,
            )
        self.active = False
        self.z_level = "overlay"
        self.scope = "workspace"
        self._host_paused = False
        self._closed = False
        self._render_broker: Any | None = None
        self._render_handle = ""
        self._last_frame = b""
        self._render_timer = QtCore.QTimer(self.layer or self)
        self._render_timer.setInterval(200)
        self._refresh = self._refresh_rendered
        self._render_timer.timeout.connect(self._refresh)
        main_window.installEventFilter(self)
        stack = getattr(main_window, "_stack", None)
        if stack is not None:
            stack.currentChanged.connect(self._page_changed)
        settings = getattr(main_window, "_settings", None)
        self._host_paused = bool(getattr(settings, "reduced_motion", False))
        if self.layer is not None:
            self.layer.hide()

    def _require_layer(self) -> _ForegroundLayer:
        """取图层；宿主没 centralWidget 时明确报错，而不是静默什么都不做。"""
        if self.layer is None:
            raise ValueError(_("宿主未提供 centralWidget，前景层不可用"))
        return self.layer

    # ---- Qt 事件 ----

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802 - Qt 命名
        if watched is self.main_window and event.type() in {
            QtCore.QEvent.Type.Resize,
            QtCore.QEvent.Type.Move,
            QtCore.QEvent.Type.Show,
        }:
            QtCore.QTimer.singleShot(0, self._sync_geometry)
        return False

    def _page_changed(self, _index: int) -> None:
        if self.scope == "canvas":
            QtCore.QTimer.singleShot(0, self._sync_geometry)

    # ---- 几何与层级 ----

    def _sync_geometry(self) -> None:
        central = self.main_window.centralWidget()
        if central is None or self.layer is None:  # pragma: no cover
            return
        target: QtWidgets.QWidget | None = None
        if self.scope == "canvas":
            stack = getattr(self.main_window, "_stack", None)
            if stack is not None:
                target = stack.currentWidget()
        if target is None and self.scope == "workspace":
            target = central
        if target is None:
            self.layer.setGeometry(central.rect())
        else:
            top_left = target.mapTo(central, QtCore.QPoint(0, 0))
            self.layer.setGeometry(
                top_left.x(), top_left.y(), target.width(), target.height()
            )
        if self.active:
            self._apply_z_level()

    def _apply_z_level(self) -> None:
        """按档位在**中央区内**排序。

        `underlay` 用 `stackUnder` 压到 central 的第一个子部件之下；
        其余用 `raise_()`。两者都只在 central 的子层级内生效。
        """
        layer = self._require_layer()
        if self.z_level == "underlay":
            central = self.main_window.centralWidget()
            children = (
                central.findChildren(
                    QtWidgets.QWidget,
                    options=QtCore.Qt.FindChildOption.FindDirectChildrenOnly,
                )
                if central is not None
                else []
            )
            siblings = [child for child in children if child is not layer]
            if siblings:
                layer.stackUnder(siblings[0])
            else:  # pragma: no cover - 空中央区
                layer.lower()
            return
        layer.raise_()

    # ---- 内容 ----

    def set_rendered(self, render_broker: Any, handle: str) -> None:
        """使用宿主**已渲染**的 PNG 帧（插件不能在宿主里执行任何绘制代码）。"""
        self._render_broker = render_broker
        self._render_handle = handle
        self._last_frame = render_broker.read_output(handle)
        self._apply_frame(self._last_frame)
        if render_broker.is_live(handle) and not self._host_paused:
            self._render_timer.start()
        else:
            self._render_timer.stop()

    def set_solid(self, color: str, opacity: int) -> None:
        """纯色 + 不透明度。只接受 `#RRGGBB`/`#RRGGBBAA` 或具名色。

        **不接受 QSS / HTML / 渐变字符串** —— 与背景层同一条边界。
        """
        value = QtGui.QColor(str(color))
        if not value.isValid():
            raise ValueError(_("前景层颜色非法（仅接受 #RRGGBB/#RRGGBBAA 或具名色）"))
        layer = self._require_layer()
        layer.pixmap = None
        layer.solid = value
        layer.opacity = max(0, min(100, int(opacity))) / 100
        self._render_timer.stop()
        layer.update()

    def _apply_frame(self, frame: bytes) -> None:
        # ★ 先验 PNG magic，再交给 Qt：`loadFromData(x, b"PNG")` 对非 PNG 字节
        #   抛的是 PySide 的参数错误（"called with wrong argument values"），
        #   而不是返回 False —— 那条路径会让我们的校验消息永远不出现。
        #   显式检查 magic 同时也更诚实：我们只接受 PNG。
        pixmap = QtGui.QPixmap()
        if not frame or not frame.startswith(PNG_MAGIC) or not pixmap.loadFromData(frame):
            raise ValueError(_("前景层渲染帧不是有效 PNG"))
        layer = self._require_layer()
        layer.pixmap = pixmap
        layer.solid = None
        layer.opacity = 1.0
        layer.update()

    def _refresh_rendered(self) -> None:
        if self._render_broker is None or not self._render_handle or self._host_paused:
            return
        try:
            frame = self._render_broker.read_output(self._render_handle)
        except ValueError:
            self._render_timer.stop()
            return
        if frame != self._last_frame:
            self._last_frame = frame
            self._apply_frame(frame)

    # ---- 状态 ----

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"z", "scope", "visible"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(_(f"前景层包含未知配置: {sorted(unknown)}"))
        if "z" in payload:
            level = str(payload["z"]).strip().lower()
            if level not in FOREGROUND_Z_LEVELS:
                allowed_levels = "/".join(FOREGROUND_Z_LEVELS)
                raise ValueError(
                    _(f"前景层 z 必须是 {allowed_levels} 之一，收到 {level!r}")
                )
            self.z_level = level
        if "scope" in payload:
            scope = str(payload["scope"]).strip().lower()
            if scope not in FOREGROUND_SCOPES:
                allowed_scopes = "/".join(FOREGROUND_SCOPES)
                raise ValueError(
                    _(f"前景层 scope 必须是 {allowed_scopes} 之一，收到 {scope!r}")
                )
            self.scope = scope
            self._sync_geometry()
        if "visible" in payload:
            if bool(payload["visible"]):
                self.enable()
            else:
                self.disable()
        return self.state()

    def state(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "z": self.z_level,
            "scope": self.scope,
            "paused": self._host_paused,
            "input_passthrough": True,
        }

    def set_paused(self, paused: bool) -> None:
        """宿主侧暂停（`reduced_motion` / 高对比）。**只暂停动画，不隐藏内容**。"""
        if paused == self._host_paused:
            return
        self._host_paused = paused
        if paused:
            self._render_timer.stop()
        elif self._render_broker is not None and self._render_handle:
            self._render_timer.start()

    def enable(self) -> bool:
        if self.active:
            self._apply_z_level()
            return True
        layer = self._require_layer()
        self.active = True
        self._sync_geometry()
        self._apply_z_level()
        layer.show()
        return True

    def disable(self) -> None:
        self._render_timer.stop()
        self.active = False
        if self.layer is not None:
            self.layer.hide()

    def capabilities(self) -> dict[str, Any]:
        """向插件描述**宿主允许的前景层词汇**（越界即 JSON 之外的错误给不出线索）。"""
        return {
            "version": 1,
            "z_levels": list(FOREGROUND_Z_LEVELS),
            "scopes": list(FOREGROUND_SCOPES),
            "content_kinds": list(FOREGROUND_CONTENT_KINDS),
            "input_passthrough": True,
            "host_owned_rendering": True,
            "interaction": "use view.action (declarative components), not layers",
            "cannot_cover": ["menu_bar", "dock_widgets", "dialogs", "status_bar"],
        }

    def close(self) -> None:
        """卸载清理：停计时器、断渲染、隐藏并销毁图层。

        ★ 必须 `deleteLater()`：只 `hide()` 会让插件卸载后浮层留在内存里，
          重载同名插件时叠出第二个（用户看到"关不掉的幽灵"）。
        """
        self._render_timer.stop()
        # 幂等：`close()` 必须可重复调（面板关闭 + 插件卸载两条路径都会走到）。
        # `QTimer.timeout` 是**同一个连接**，二次 `disconnect()` 会让 libpyside
        # 打 RuntimeWarning；用标志位控制，别指望异常能兜住（它只警告不抛）。
        if not self._closed:
            try:
                self._render_timer.timeout.disconnect(self._refresh)
            except (RuntimeError, TypeError):  # pragma: no cover - 宿主侧已释放
                pass
            self._closed = True
        if self._render_broker is not None and self._render_handle:
            try:
                self._render_broker.stop_live()
            except (OSError, ValueError):  # pragma: no cover - 宿主侧已释放
                pass
        self._render_broker = None
        self._render_handle = ""
        self._last_frame = b""
        self.active = False
        if self.layer is not None:
            self.layer.hide()
            self.layer.setParent(None)
            self.layer.deleteLater()
            self.layer = None


class ForegroundSurfaceService:
    """插件的 `surface.foreground.*` 能力实现（broker 调用它）。"""

    def __init__(self, controller: ForegroundController) -> None:
        self._controller = controller

    @property
    def controller(self) -> ForegroundController:
        return self._controller

    def show_rendered(self, render_broker: Any, handle: str) -> dict[str, Any]:
        self._controller.set_rendered(render_broker, handle)
        self._controller.enable()
        return self._controller.state()

    def show_solid(self, color: str, opacity: int) -> dict[str, Any]:
        self._controller.set_solid(color, opacity)
        self._controller.enable()
        return self._controller.state()

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._controller.configure(payload)

    def clear(self) -> dict[str, Any]:
        self._controller.disable()
        return self._controller.state()

    def capabilities(self) -> dict[str, Any]:
        return self._controller.capabilities()

    def close(self) -> None:
        self._controller.close()
