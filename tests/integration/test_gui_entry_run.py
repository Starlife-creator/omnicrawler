"""N2：经 GUI **真实入口**（运行按钮）创建并启动任务 → 子进程 → 结果页可见。

与 ``test_gui_worker_local_task.py`` 的分工：那边直接驱动 ``WorkerTaskRunner``（运行器层），
本文件走用户真正点的那条路——工具栏「运行」按钮 → ``MainWindow._request_run``
→ 配置校验与「试跑一致」闸门 → ``RunController.run_task`` → worker 子进程 → 结果页。

诚实声明（覆盖边界）：
- 用 ``QPushButton.click()`` 触发**真实信号链**，不伪造槽函数调用；
- 试跑结果经画布官方 API ``set_trial_result()`` 注入（试跑本身由
  ``test_first_task_journey.py`` 覆盖），**未点击真实鼠标**；
- 不覆盖便携产物内运行（按约定留 CI / 受控环境）。
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import psutil  # noqa: E402

from omnicrawler.gui.core.config_serializer import load_yaml  # noqa: E402

EXPECTED = (("苹果", "11"), ("香蕉", "22"), ("樱桃", "33"))

_LIST_HTML = """<html><body><div class="list">
<div class="item"><h2 class="t">苹果</h2><span class="p">11</span></div>
<div class="item"><h2 class="t">香蕉</h2><span class="p">22</span></div>
<div class="item"><h2 class="t">樱桃</h2><span class="p">33</span></div>
</div></body></html>"""


def _make_handler(page: str):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # noqa: N802
            return

    return _Handler


@contextlib.contextmanager
def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(_LIST_HTML))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/list"
    finally:
        server.shutdown()
        server.server_close()


def _config_yaml(seed: str, workspace: Path) -> str:
    """用户在 GUI 里创建的那份配置（字段名用产品自身产出的中文键）。"""
    return f"""project:
  name: gui-entry
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


def _pump(app, predicate, *, timeout: float = 180.0, what: str) -> None:
    """条件等待（不依赖固定睡眠）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.05)
    assert predicate(), f"等待超时：{what}"


def test_gui_run_button_starts_task_and_shows_results(tmp_path: Path, monkeypatch) -> None:
    """点真实「运行」按钮 → worker 子进程 → 状态「已完成」→ 结果页看到真值。"""
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow

    app = QApplication.instance() or QApplication([])

    # 让首次启动向导不弹（与既有 GUI 测试同款做法）。
    # 必须用 monkeypatch：直接给类属性赋值会**泄漏**给同进程的后续用例。
    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)

    window = MainWindow()
    window._project_root = tmp_path
    window._rebuild_project_components()

    # 项目根指向临时目录，避免任何真实工程被写入
    assert Path(window._project_root) == tmp_path

    workspace = (tmp_path / "ws").resolve()
    with _serve() as seed:
        cfg_path = tmp_path / "task.yaml"
        cfg_path.write_text(_config_yaml(seed, workspace), encoding="utf-8")
        config = load_yaml(cfg_path)

        # 把配置交给画布（等价于用户在 GUI 里编辑出来的这份配置）
        window._config = config
        window._config_path = cfg_path
        window._task_canvas.load_config(config)
        # 反向护栏：尚未试跑时运行按钮应**不可用** —— 证明下面的启动确实通过了试跑闸门，
        # 而不是因为闸门形同虚设才"点得动"。
        assert not window._task_canvas._run_btn.isEnabled(), "未试跑时运行按钮应禁用"
        # 试跑后的回调（官方 API）：绑定试跑时的配置指纹，解除「开始全量运行」闸门
        window._task_canvas.set_trial_result(True, "试跑通过：3 条记录", {})

        # 真实 UI 前置条件：闸门满足 ⇒ 按钮可用
        assert window._task_canvas._run_btn.isEnabled(), "试跑一致 + 交付有效后运行按钮应可用"

        # 避免测试触发打开文件管理器 / 自动导出等副作用
        window._omnicrawler_available = True
        with contextlib.suppress(Exception):
            window._settings.auto_open_result = False
            window._settings.markdown_export_enabled = False
            window._settings.sound_enabled = False

        try:
            # —— 真实按钮点击：走完整信号链 ——
            window._run_btn.click()
            app.processEvents()

            pid = window._task_runner.get_pid()
            assert pid and pid != os.getpid(), "点运行后应由独立 worker 子进程执行"
            assert window._task_runner.state in {"running", "retrying"}, (
                f"点运行后应处于运行态：{window._task_runner.state}"
            )
            assert psutil.pid_exists(pid)

            # 等任务结束（终态）
            _pump(
                app,
                lambda: window._task_runner.state in {"finished", "error"},
                what="任务到达终态",
            )
            assert window._task_runner.state == "finished", (
                f"任务应成功结束：{window._task_runner.state}"
            )
            assert window._status_text.text() == "已完成", (
                f"状态栏应显示已完成：{window._status_text.text()}"
            )

            # —— 结果可见：结果页自动加载到本次 work 区的 records.csv ——
            records_csv = workspace / "output" / "records.csv"
            assert records_csv.is_file(), (
                f"应产出 records.csv：{sorted(p.name for p in (workspace / 'output').glob('*'))}"
            )
            assert window._result_table.current_path == records_csv, (
                f"结果页应加载本次产物：{window._result_table.current_path}"
            )
            # CSV 索引是异步的，等到行数到位
            _pump(
                app,
                lambda: window._result_table._model.total_rows == len(EXPECTED),
                timeout=60,
                what="结果页行数到位",
            )

            # 内容对得上真值（直接核对产物，避免依赖表格单元格渲染）
            import csv as _csv

            rows = list(_csv.DictReader(records_csv.open(encoding="utf-8-sig")))
            got = {(r["标题"], r["价格"]) for r in rows}
            assert got == set(EXPECTED), f"交付内容应等于真值：{got}"
            assert len(rows) == len(EXPECTED), f"应恰好交付 3 条：{len(rows)}"

            # 任务历史留痕（GUI 自己记录的运行归属）
            assert window._running_task_id is None, "结束后应清空当前运行归属"
        finally:
            with contextlib.suppress(Exception):
                window._task_runner._backend.shutdown()
            gone = time.monotonic() + 30
            while time.monotonic() < gone and pid and psutil.pid_exists(pid):
                app.processEvents()
                time.sleep(0.1)
            with contextlib.suppress(Exception):
                window._task_runner._poller.stop()
            window.close()
            app.processEvents()
    assert not (pid and psutil.pid_exists(pid)), f"结束后 worker 子进程 {pid} 未退出"
