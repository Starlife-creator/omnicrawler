"""N2 闭环：GUI 侧运行器 → 真实 worker 子进程 → 本地 HTTP 任务 → 结果可见 → 进程退出。

**为什么单开一个文件**：这条链此前的覆盖都被绕开了——

- ``tests/integration/sdk/test_execution_backend.py`` 只验**控制面**：种子指向不可达地址，
  断言握手、会话文件、pause/resume/shutdown，**不断言任务产出**。
- ``tests/integration/sdk/test_worker_task_runner.py`` 用**假 backend**，只验 Qt 信号接线。
- ``tests/integration/test_first_task_journey.py`` 明确声明"不经真实点击与子进程"。

因此"在 GUI 里点运行、真的采到数据、且数据对得上真值"此前没有证据。本文件补这一环，
**全部离线可控**：本地固定 HTTP 站点 + 应用自有的 worker 子进程（不依赖公网、不依赖打包产物）。

它同时是配置往返修复（`save_yaml` 不得用硬编码默认值覆盖透传键）的端到端护栏：
若 ``extract.item_selector`` 在 GUI 往返中被清空，本用例会从 3 条退化成 1 条而失败。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import psutil  # noqa: E402

from omnicrawler.gui.core.config_serializer import load_yaml  # noqa: E402

#: 固定真值。以 (标题, 价格) 集合比对，避免依赖抓取顺序。
EXPECTED = (
    ("苹果", "11"),
    ("香蕉", "22"),
    ("樱桃", "33"),
)

_LIST_HTML = """<html><body><div class="list">
<div class="item"><h2 class="t">苹果</h2><span class="p">11</span></div>
<div class="item"><h2 class="t">香蕉</h2><span class="p">22</span></div>
<div class="item"><h2 class="t">樱桃</h2><span class="p">33</span></div>
</div></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 —— http.server 的回调命名约定
        body = _LIST_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # 静音访问日志，避免污染测试输出
        return


@pytest.fixture()
def local_site():
    """本地固定站点：绑定 127.0.0.1 的随机端口。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/list"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def _worker_socket_path_ok(tmp_path: Path) -> None:
    """对齐 sdk 测试的既有护栏：POSIX AF_UNIX 路径过长会超 108 字节上限。"""
    if os.name == "nt":
        return
    import socket as socket_module

    if not hasattr(socket_module, "AF_UNIX"):
        pytest.skip("平台无 AF_UNIX 支持")
    workspace = tmp_path / "ws"
    address = str(workspace / f".worker-{uuid.uuid4().hex}.sock")
    if len(address.encode("utf-8")) >= 104:
        pytest.skip("AF_UNIX socket 路径过长（CI 长工作区），跳过实时握手测试")


def _config_yaml(seed: str, workspace: Path) -> str:
    """用户手上那份可用配置（例如 auto-analyze 产出或模板配置）。"""
    return f"""project:
  name: gui-loop
  workspace: {workspace.as_posix()}
source:
  kind: static_html
  seeds: [{seed}]
crawl: {{max_pages: 5, concurrency: 1, same_host: true}}
http:
  respect_robots: false
  delay_seconds: 0.0
  timeout_seconds: 10
  retries: 0
  allow_private_network: true
extract:
  mode: html
  item_selector: div.item
  fields:
    标题: {{selector: h2.t}}
    价格: {{selector: span.p}}
outputs: {{jsonl: true, csv: true, xlsx: false}}
"""


@pytest.mark.usefixtures("_worker_socket_path_ok")
def test_gui_run_reaches_local_site_and_delivers_verified_records(
    tmp_path: Path, local_site: str
) -> None:
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.runner.worker_task_runner import WorkerTaskRunner

    app = QApplication.instance() or QApplication([])

    workspace = (tmp_path / "ws").resolve()
    cfg_path = tmp_path / "provided.yaml"
    cfg_path.write_text(_config_yaml(local_site, workspace), encoding="utf-8")

    # 走 GUI 的加载路径：未建模键（item_selector）应进入 passthrough 并在保存时保真
    config = load_yaml(cfg_path)
    assert config.passthrough["extract"]["item_selector"] == "div.item", (
        "GUI 加载后不应改写列表项选择器"
    )

    # 真实 backend（不注入替身）：任务必须由独立子进程执行
    runner = WorkerTaskRunner(project_root=tmp_path)
    logs: list[str] = []
    finished: list[tuple[str, int]] = []
    runner.log_line.connect(lambda text, _level: logs.append(text))
    runner.task_finished.connect(lambda task, code: finished.append((task, code)))

    try:
        assert runner.start(config) is True, f"启动失败，日志={logs}"

        pid = runner.get_pid()
        assert pid, "应拿到 worker 子进程 pid"
        assert pid != os.getpid(), "任务必须在独立子进程中执行，而不是当前进程"
        assert psutil.pid_exists(pid), f"worker 子进程 {pid} 应处于存活状态"

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not finished:
            app.processEvents()
            time.sleep(0.05)

        assert finished, f"任务未在时限内结束：state={runner.state} 末尾日志={logs[-5:]}"
        assert finished[0][1] == 0, f"退出码应为 0：{finished}"
        assert not any("0 条记录" in text for text in logs), "不应出现零记录告警"

        # —— 结果可见：产物落在工作区，且内容对得上真值 ——
        records_path = workspace / "output" / "records.jsonl"
        assert records_path.is_file(), f"应有产物文件，实际：{sorted(p.name for p in (workspace / 'output').glob('*'))}"
        records = [
            json.loads(line)
            for line in records_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        got = [(r["data"]["标题"], r["data"]["价格"]) for r in records]
        assert len(got) == len(EXPECTED), f"应恰好交付 {len(EXPECTED)} 条（无漏采、无额外、无重复）：{got}"
        assert set(got) == set(EXPECTED), f"真值不符：got={got}"
        # 来源可追溯：每条记录都指向本地目标页
        assert {r["source_url"] for r in records} == {local_site}, "每条记录都应带正确的来源 URL"
        assert (workspace / "output" / "records.csv").is_file(), "应同时产出 CSV"

        # —— 进程正确退出：不留后台残留 ——
        runner._backend.shutdown()
        gone_deadline = time.monotonic() + 30
        while time.monotonic() < gone_deadline and psutil.pid_exists(pid):
            app.processEvents()
            time.sleep(0.1)
        assert not psutil.pid_exists(pid), f"worker 子进程 {pid} 未退出，存在资源残留"
    finally:
        runner._poller.stop()
        app.processEvents()
