"""Isolated, host-owned rendering for local declarative plugin resources."""

from __future__ import annotations

import mimetypes
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_RENDER_OUTPUTS = 16
MIN_VIEWPORT = 240
MAX_VIEWPORT = 3840
MAX_LIVE_WIDTH = 1920
MAX_LIVE_HEIGHT = 1080
LIVE_FRAME_INTERVAL = 0.2
MAX_ASSET_COUNT = 256
MAX_ASSET_TOTAL_BYTES = 64 * 1024 * 1024
AssetLoader = Callable[[str], tuple[bytes, str]]


class BrowserRuntimeManager:
    """Apply one conservative policy to every plugin-owned browser render."""

    def snapshot(
        self,
        html: str,
        *,
        width: int,
        height: int,
        scripted: bool,
        base_url: str,
        asset_loader: AssetLoader,
    ) -> bytes:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("当前安装不包含浏览器渲染运行时") from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--disable-extensions", "--disable-background-networking"],
            )
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    java_script_enabled=bool(scripted),
                    service_workers="block",
                    accept_downloads=False,
                    offline=True,
                )
                page = context.new_page()
                page.set_default_timeout(5_000)
                if scripted:
                    self._harden_page(page)
                self._bind_routes(page, asset_loader)
                page.set_content(
                    f'<base href="{base_url}">' + html,
                    wait_until="domcontentloaded",
                    timeout=5_000,
                )
                png = page.screenshot(type="png", animations="disabled", timeout=5_000)
                context.close()
            finally:
                browser.close()
        return png

    def stream(
        self,
        html: str,
        *,
        width: int,
        height: int,
        stop: threading.Event,
        on_frame: Any,
        base_url: str,
        asset_loader: AssetLoader,
    ) -> None:
        """Capture a bounded, non-interactive, offline scripted page at at most 5 FPS."""

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--disable-extensions", "--disable-background-networking"],
            )
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    java_script_enabled=True,
                    service_workers="block",
                    accept_downloads=False,
                    offline=True,
                )
                page = context.new_page()
                page.set_default_timeout(5_000)
                self._harden_page(page)
                self._bind_routes(page, asset_loader)
                page.set_content(
                    f'<base href="{base_url}">' + html,
                    wait_until="domcontentloaded",
                    timeout=5_000,
                )
                while not stop.is_set():
                    started = time.monotonic()
                    on_frame(page.screenshot(type="png", timeout=5_000))
                    stop.wait(max(0.0, LIVE_FRAME_INTERVAL - (time.monotonic() - started)))
                context.close()
            finally:
                browser.close()

    @staticmethod
    def _bind_routes(page: Any, asset_loader: AssetLoader) -> None:
        def route_request(route: Any) -> None:
            parsed = urlsplit(route.request.url)
            if parsed.scheme != "https" or parsed.hostname != "plugin-resource.invalid":
                route.abort()
                return
            try:
                body, content_type = asset_loader(unquote(parsed.path.lstrip("/")))
            except (OSError, ValueError):
                route.abort()
                return
            route.fulfill(status=200, body=body, content_type=content_type)

        page.route("**/*", route_request)

    @staticmethod
    def _harden_page(page: Any) -> None:
        script = """
        (() => {
          const blocked = class { constructor() { throw new Error('network disabled'); } };
          for (const name of ['WebSocket', 'EventSource', 'RTCPeerConnection',
                              'webkitRTCPeerConnection']) {
            try { Object.defineProperty(globalThis, name, {value: blocked, configurable: false}); }
            catch (_) {}
          }
          try { Object.defineProperty(navigator, 'sendBeacon', {value: () => false}); }
          catch (_) {}
        })();
        """
        page.add_init_script(script)
        page.evaluate(script)


class RenderBroker:
    """Render local HTML to opaque PNG outputs without exposing host paths."""

    def __init__(self, runtime: BrowserRuntimeManager | None = None) -> None:
        self._outputs: dict[str, bytes] = {}
        self._lock = threading.RLock()
        self._runtime = runtime or BrowserRuntimeManager()
        self._live_stop: threading.Event | None = None
        self._live_thread: threading.Thread | None = None
        self._live_handle = ""
        self._live_error = ""

    def snapshot_html(
        self,
        resource_broker: Any,
        handle: str,
        relative: str,
        *,
        width: int = 1920,
        height: int = 1080,
        scripted: bool = False,
    ) -> dict[str, Any]:
        bounded_width = self._dimension(width, "width")
        bounded_height = self._dimension(height, "height")
        source = resource_broker.read(handle, relative, maximum_bytes=MAX_HTML_BYTES)
        try:
            html = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("本地 HTML 必须使用 UTF-8 编码") from exc

        png = self._runtime.snapshot(
            html,
            width=bounded_width,
            height=bounded_height,
            scripted=scripted,
            base_url=self._base_url(relative),
            asset_loader=self._asset_loader(resource_broker, handle),
        )

        output_handle = "render:" + secrets.token_urlsafe(24)
        with self._lock:
            if len(self._outputs) >= MAX_RENDER_OUTPUTS:
                oldest = next(iter(self._outputs))
                self._outputs.pop(oldest, None)
            self._outputs[output_handle] = png
        return {
            "handle": output_handle,
            "media_type": "image/png",
            "width": bounded_width,
            "height": bounded_height,
            "scripted": bool(scripted),
        }

    def start_html_live(
        self,
        resource_broker: Any,
        handle: str,
        relative: str,
        *,
        width: int = 1280,
        height: int = 720,
    ) -> dict[str, Any]:
        bounded_width = self._dimension(width, "width")
        bounded_height = self._dimension(height, "height")
        if bounded_width > MAX_LIVE_WIDTH or bounded_height > MAX_LIVE_HEIGHT:
            raise ValueError("动态渲染分辨率上限为 1920x1080")
        source = resource_broker.read(handle, relative, maximum_bytes=MAX_HTML_BYTES)
        try:
            html = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("本地 HTML 必须使用 UTF-8 编码") from exc
        self.stop_live()
        output_handle = "render:" + secrets.token_urlsafe(24)
        stop = threading.Event()
        ready = threading.Event()
        self._live_stop = stop
        self._live_handle = output_handle
        self._live_error = ""

        def on_frame(frame: bytes) -> None:
            with self._lock:
                self._outputs[output_handle] = frame
            ready.set()

        def run() -> None:
            try:
                self._runtime.stream(
                    html,
                    width=bounded_width,
                    height=bounded_height,
                    stop=stop,
                    on_frame=on_frame,
                    base_url=self._base_url(relative),
                    asset_loader=self._asset_loader(resource_broker, handle),
                )
            except Exception as exc:  # noqa: BLE001 - worker error crosses as bounded text
                self._live_error = f"{type(exc).__name__}: {exc}"[:500]
                ready.set()

        self._live_thread = threading.Thread(target=run, name="plugin-html-render", daemon=True)
        self._live_thread.start()
        if not ready.wait(7.0) or self._live_error:
            self.stop_live()
            raise RuntimeError(self._live_error or "动态 HTML 首帧渲染超时")
        return {
            "handle": output_handle,
            "media_type": "image/png-sequence",
            "width": bounded_width,
            "height": bounded_height,
            "fps_limit": round(1 / LIVE_FRAME_INTERVAL),
        }

    def is_live(self, handle: str) -> bool:
        return bool(handle) and handle == self._live_handle

    def stop_live(self) -> None:
        stop, thread = self._live_stop, self._live_thread
        if stop is not None:
            stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=6.0)
        self._live_stop = None
        self._live_thread = None
        self._live_handle = ""

    def read_output(self, handle: str) -> bytes:
        with self._lock:
            data = self._outputs.get(str(handle))
        if data is None:
            raise ValueError("渲染结果句柄不存在或已失效")
        return data

    def close(self) -> None:
        self.stop_live()
        with self._lock:
            self._outputs.clear()

    @staticmethod
    def _base_url(relative: str) -> str:
        parent = PurePosixPath(str(relative).replace("\\", "/")).parent.as_posix()
        suffix = "" if parent == "." else parent.strip("/") + "/"
        return "https://plugin-resource.invalid/" + suffix

    @staticmethod
    def _asset_loader(resource_broker: Any, handle: str) -> AssetLoader:
        cache: dict[str, tuple[bytes, str]] = {}
        total = [0]

        def load(relative: str) -> tuple[bytes, str]:
            clean = str(relative).replace("\\", "/").lstrip("/")
            if clean in cache:
                return cache[clean]
            if len(cache) >= MAX_ASSET_COUNT:
                raise ValueError("本地网页资源数量超过上限")
            data = resource_broker.read(handle, clean)
            total[0] += len(data)
            if total[0] > MAX_ASSET_TOTAL_BYTES:
                raise ValueError("本地网页资源总量超过上限")
            content_type = mimetypes.guess_type(clean)[0] or "application/octet-stream"
            cache[clean] = (data, content_type)
            return cache[clean]

        return load

    @staticmethod
    def _dimension(value: Any, field: str) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"渲染 {field} 必须是整数") from exc
        if not MIN_VIEWPORT <= result <= MAX_VIEWPORT:
            raise ValueError(f"渲染 {field} 超出安全范围")
        return result
