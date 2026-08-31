"""Generic host-owned media surfaces for isolated declarative plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6 import QtCore

from .background_host import BackgroundController


@dataclass(frozen=True, slots=True)
class _SurfaceRegistration:
    background_id: str
    label: str
    default_opacity: float = 0.24
    default_dim: float = 0.30


class MediaSurfaceService:
    """A plugin-scoped facade over a host-rendered background layer."""

    def __init__(self, main_window: Any, plugin_id: str, title: str) -> None:
        self._controller = BackgroundController(
            main_window,
            _SurfaceRegistration(f"declarative-{plugin_id}", title),
        )
        self._render_timer = QtCore.QTimer(self._controller.layer)
        self._render_timer.setInterval(200)
        self._render_timer.timeout.connect(self._refresh_rendered)
        self._render_broker: Any | None = None
        self._render_handle = ""
        self._last_frame = b""

    @property
    def controller(self) -> BackgroundController:
        return self._controller

    def set_media(self, resource_broker: Any, handle: str, relative: str) -> None:
        self._controller.set_media(resource_broker.resolve_media(handle, relative))

    def set_rendered(self, render_broker: Any, handle: str) -> None:
        self._render_broker = render_broker
        self._render_handle = handle
        self._last_frame = render_broker.read_output(handle)
        self._controller.set_rendered_image(self._last_frame)
        if render_broker.is_live(handle):
            self._render_timer.start()

    def _refresh_rendered(self) -> None:
        if self._render_broker is None or not self._render_handle:
            return
        try:
            frame = self._render_broker.read_output(self._render_handle)
        except ValueError:
            self._render_timer.stop()
            return
        if frame != self._last_frame:
            self._last_frame = frame
            self._controller.set_rendered_image(frame)

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"opacity", "dim", "fit", "paused", "rotation_seconds"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"背景表面包含未知配置: {sorted(unknown)}")
        if "opacity" in payload:
            self._controller.set_opacity(int(payload["opacity"]))
        if "dim" in payload:
            self._controller.set_dim(int(payload["dim"]))
        if "fit" in payload:
            fit = str(payload["fit"])
            if fit not in {"cover", "contain", "stretch"}:
                raise ValueError("背景 fit 必须是 cover、contain 或 stretch")
            self._controller.set_fit(fit)
        if "paused" in payload:
            self._controller.set_paused(bool(payload["paused"]))
        if "rotation_seconds" in payload:
            self._controller.set_rotation(int(payload["rotation_seconds"]))
        return {
            "active": self._controller.active,
            "opacity": round(self._controller.layer.opacity * 100),
            "dim": round(self._controller.dim * 100),
            "fit": self._controller.fit_mode,
            "paused": self._controller.paused,
        }

    def clear(self) -> None:
        self._render_timer.stop()
        if self._render_broker is not None and self._render_handle:
            self._render_broker.stop_live()
        self._render_broker = None
        self._render_handle = ""
        self._last_frame = b""
        self._controller.disable()
