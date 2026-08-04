"""WebSocket 服务器 — 接收 EasySpider 浏览器扩展消息，转发到 OmniCrawler。

协议兼容 EasySpider Electron 主进程的 WebSocket 接口 (ws://localhost:8084)。

消息类型:
    type:0  → 连接注册（含页面标题）
    type:3  → 元素选择事件（pipe JSON 含 xpath/allXPaths/content）
    type:2  → 键盘输入事件

用法:
    server = VisualSelectorServer()
    server.start()
    # 打开 Chrome 并加载 EasySpider 扩展后，在网页上点选元素
    selections = server.get_selections()  # 获取全部选择结果
    server.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .field_converter import FieldConverter

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8084
DEFAULT_HOST = "localhost"


@dataclass
class SelectionEvent:
    """一次元素选择事件。"""
    action_type: str = ""        # singleClick / extractData / inputText / loopClick
    xpath: str = ""
    all_xpaths: list[str] = field(default_factory=list)
    content: str = ""            # 元素文本内容
    use_loop: bool = False
    iframe: bool = False
    tab_index: int = -1
    history: int = 0
    params: list[dict[str, Any]] = field(default_factory=list)  # 提取字段参数
    raw: dict[str, Any] = field(default_factory=dict)


class VisualSelectorServer:
    """WebSocket 服务器 — 与 EasySpider Chrome 扩展通信。"""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._selections: list[SelectionEvent] = []
        self._page_title: str = ""
        self._connected: bool = False
        self._lock = threading.Lock()
        self.converter = FieldConverter()
        self._on_selection: Callable[[SelectionEvent], None] | None = None
        self._loop: Any = None
        self._start_error: str | None = None  # A19：启动失败原因（端口占用/缺依赖）

    # ── 公共 API ──────────────────────────────────────────────────────

    def start(self, blocking: bool = False) -> bool:
        """启动 WebSocket 服务器；启动失败（端口占用/缺依赖）返回 False。"""
        if self._running:
            return True
        self._start_error = None
        self._running = True
        if blocking:
            try:
                asyncio.run(self._run_server())
            except Exception as exc:  # noqa: BLE001
                self._start_error = str(exc)
                self._running = False
        else:
            self._thread = threading.Thread(
                target=lambda: asyncio.run(self._run_server()),
                name="omnicrawl-visual-selector",
                daemon=True,
            )
            self._thread.start()
            # A19：等待启动结果（serve 成功或报错），最多 2s
            for _ in range(20):
                time.sleep(0.1)
                if self._start_error is not None or self._server is not None:
                    break
        if self._start_error:
            logger.error("可视化选择器启动失败: %s", self._start_error)
            return False
        return True

    def stop(self) -> None:
        """停止服务器。"""
        self._running = False
        if self._server:
            self._server.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def get_selections(self) -> list[SelectionEvent]:
        """获取所有累积的选择事件。"""
        with self._lock:
            return list(self._selections)

    def clear_selections(self) -> None:
        """清空选择历史。"""
        with self._lock:
            self._selections.clear()
        self.converter.clear()

    def on_selection(self, callback: Callable[[SelectionEvent], None]) -> None:
        """注册选择事件回调。"""
        self._on_selection = callback

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def page_title(self) -> str:
        return self._page_title

    # ── 内部实现 ──────────────────────────────────────────────────────

    async def _run_server(self) -> None:
        try:
            import websockets
        except ImportError:
            self._start_error = "缺少 websockets 库，请执行 pip install websockets"
            logger.error("%s", self._start_error)
            return
        self._loop = asyncio.get_event_loop()
        try:
            self._server = await websockets.serve(
                self._handle_connection,  # type: ignore[arg-type]
                self._host,
                self._port,
            )
        except Exception as exc:  # noqa: BLE001 - 端口占用等启动失败需透传
            self._start_error = str(exc)
            logger.error("可视化选择器服务启动失败: %s", exc)
            return
        logger.info("可视化选择器 WebSocket 服务已启动: ws://%s:%s", self._host, self._port)
        while self._running:
            await asyncio.sleep(0.5)
        self._server.close()
        await self._server.wait_closed()
        logger.info("可视化选择器 WebSocket 服务已关闭")

    async def _handle_connection(self, websocket: Any, path: str) -> None:
        """处理单个 WebSocket 连接。"""
        client_id = str(id(websocket))[-8:]
        logger.info("浏览器扩展已连接: %s", client_id)
        self._connected = True
        try:
            async for raw_message in websocket:
                try:
                    message = json.loads(raw_message)
                    await self._dispatch(message, websocket)
                except json.JSONDecodeError:
                    logger.warning("无效 JSON 消息")
                except Exception:
                    logger.exception("处理消息时出错")
        except Exception:
            logger.debug("浏览器扩展连接关闭", exc_info=True)
        finally:
            self._connected = False
            logger.info("浏览器扩展已断开: %s", client_id)

    async def _dispatch(self, message: dict[str, Any], websocket: Any) -> None:
        """分发消息到对应处理器。"""
        msg_type = message.get("type", -1)

        if msg_type == 0:  # 连接注册
            inner = message.get("message", {})
            self._page_title = inner.get("title", "")
            logger.info("页面标题: %s", self._page_title)
            # 回复连接确认
            await websocket.send(json.dumps({
                "type": "update_parameter_num",
                "value": "1",
            }))

        elif msg_type == 3:  # 元素选择事件
            pipe_str = message.get("message", {}).get("pipe", "{}")
            try:
                pipe_data = json.loads(pipe_str)
            except json.JSONDecodeError:
                pipe_data = {}
            event = SelectionEvent(
                action_type=pipe_data.get("type", "unknown"),
                xpath=pipe_data.get("xpath", ""),
                all_xpaths=self._normalize_xpaths(pipe_data.get("allXPaths", [])),
                content=pipe_data.get("content", pipe_data.get("text", "")),
                use_loop=pipe_data.get("useLoop", False),
                iframe=pipe_data.get("iframe", False),
                tab_index=pipe_data.get("tabIndex", -1),
                history=pipe_data.get("history", 0),
                params=pipe_data.get("params", []),
                raw=pipe_data,
            )
            with self._lock:
                self._selections.append(event)
            logger.info(
                "收到选择: type=%s xpath=%s content=%.50s",
                event.action_type, event.xpath[:60], event.content,
            )
            if self._on_selection:
                self._on_selection(event)

        elif msg_type == 2:  # 键盘输入
            pass  # 当前忽略，由浏览器侧处理

    @staticmethod
    def _normalize_xpaths(raw: Any) -> list[str]:
        """规范化 allXPaths 字段。"""
        if isinstance(raw, list):
            return [str(x) for x in raw if str(x)]
        if isinstance(raw, str) and raw:
            return [raw]
        return []


def start_server(port: int = DEFAULT_PORT, blocking: bool = False) -> VisualSelectorServer:
    """便捷函数：启动可视化选择器服务器。

    Args:
        port: WebSocket 端口（默认 8084，兼容 EasySpider 扩展）。
        blocking: True 则阻塞当前线程直到服务器停止。

    Returns:
        VisualSelectorServer 实例。
    """
    server = VisualSelectorServer(port=port)
    server.start(blocking=blocking)
    return server


# ── CLI ────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="OmniCrawler 可视化选择器 WebSocket 服务器")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"WebSocket 端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--output", "-o", help="收到选择后写入的 YAML 文件路径")
    args = parser.parse_args()

    server = start_server(port=args.port)
    print(f"\n✓ 可视化选择器已启动: ws://localhost:{args.port}")
    print("  打开 Chrome 并加载 EasySpider 扩展即可在网页上点选元素")
    print("  按 Ctrl+C 停止\n")

    try:
        while True:
            time.sleep(1)
            selections = server.get_selections()
            if selections and args.output:
                latest = selections[-1]
                converter = FieldConverter()
                converter.add_selection(
                    [{"xpath": latest.xpath, "allXPaths": latest.all_xpaths, "text": latest.content}],
                    common_xpath=latest.xpath,
                )
                config = converter.to_yaml()
                import yaml
                with open(args.output, "w", encoding="utf-8") as fh:
                    fh.write("# Generated by OmniCrawler Visual Selector\n")
                    yaml.dump(config, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)
                print(f"  → 已更新: {args.output}")
    except KeyboardInterrupt:
        print("\n正在关闭...")
    finally:
        server.stop()
        print("已关闭")


if __name__ == "__main__":
    main()
