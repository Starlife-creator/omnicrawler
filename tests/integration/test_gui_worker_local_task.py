"""N2 闭环：GUI 侧运行器 → 真实 worker 子进程 → 本地 HTTP 任务 → 结果可见 → 进程退出。

**为什么单开一个文件**：这条链此前的覆盖都被绕开了——

- ``tests/integration/sdk/test_execution_backend.py`` 只验**控制面**：种子指向不可达地址，
  断言握手、会话文件、pause/resume/shutdown，**不断言任务产出**。
- ``tests/integration/sdk/test_worker_task_runner.py`` 用**假 backend**，只验 Qt 信号接线。
- ``tests/integration/test_first_task_journey.py`` 明确声明"不经真实点击与子进程"。

因此"在 GUI 里点运行、真的采到数据、且数据对得上真值"此前没有证据。本文件补这一环，
**全部离线可控**：本地固定 HTTP 站点 + 应用自有的 worker 子进程（不依赖公网、不依赖打包产物）。

覆盖两条账本验收线：
1. 单页列表 → JSONL/CSV 真值 + 进程退出；
2. 列表 → 详情两级（``source.kind: crawl``，4 页 6 条）→ 来源覆盖 4 个 URL + **XLSX 可重新打开**。

它同时是配置往返修复（``save_yaml`` 不得用硬编码默认值覆盖透传键）的端到端护栏：
若 ``extract.item_selector`` 在 GUI 往返中被清空，用例 1 会从 3 条退化成 1 条而失败。
"""

from __future__ import annotations

import contextlib
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

#: 用例 1 真值：单页列表 3 条。以集合比对，避免依赖抓取顺序。
SINGLE_PAGE_EXPECTED = (("苹果", "11"), ("香蕉", "22"), ("樱桃", "33"))

#: 用例 2 真值：列表 3 条 + 3 个详情页各 1 条，共 6 条（列表与详情内容刻意不同，避免重复）。
TWO_LEVEL_EXPECTED = (
    ("列表1", "11"),
    ("列表2", "22"),
    ("列表3", "33"),
    ("明细一", "10"),
    ("明细二", "20"),
    ("明细三", "30"),
)

_SINGLE_PAGE = {
    "/list": """<html><body><div class="list">
<div class="item"><h2 class="t">苹果</h2><span class="p">11</span></div>
<div class="item"><h2 class="t">香蕉</h2><span class="p">22</span></div>
<div class="item"><h2 class="t">樱桃</h2><span class="p">33</span></div>
</div></body></html>"""
}

_TWO_LEVEL = {
    "/list": """<html><body><div class="list">
<div class="item"><h2 class="t">列表1</h2><span class="p">11</span></div>
<div class="item"><h2 class="t">列表2</h2><span class="p">22</span></div>
<div class="item"><h2 class="t">列表3</h2><span class="p">33</span></div>
</div><nav>
<a href="/detail-1">明细一</a><a href="/detail-2">明细二</a><a href="/detail-3">明细三</a>
</nav></body></html>""",
    "/detail-1": '<html><body><div class="item"><h2 class="t">明细一</h2><span class="p">10</span></div></body></html>',
    "/detail-2": '<html><body><div class="item"><h2 class="t">明细二</h2><span class="p">20</span></div></body></html>',
    "/detail-3": '<html><body><div class="item"><h2 class="t">明细三</h2><span class="p">30</span></div></body></html>',
}


def _make_handler(pages: dict[str, str]):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 —— http.server 的回调命名约定
            page = pages.get(self.path)
            if page is None:
                self.send_error(404)
                return
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # 静音访问日志，避免污染测试输出
            return

    return _Handler


@contextlib.contextmanager
def _serve(pages: dict[str, str]):
    """本地固定站点：绑定 127.0.0.1 的随机端口。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(pages))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
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


def _config_yaml(seed: str, workspace: Path, *, source_kind: str, max_depth: int, xlsx: bool) -> str:
    """用户手上那份可用配置（例如 auto-analyze 产出或模板配置）。"""
    return f"""project:
  name: gui-loop
  workspace: {workspace.as_posix()}
source:
  kind: {source_kind}
  seeds: [{seed}]
crawl: {{max_pages: 12, max_depth: {max_depth}, concurrency: 1, same_host: true}}
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
outputs: {{jsonl: true, csv: true, xlsx: {str(xlsx).lower()}}}
"""


def _drive_gui_worker(tmp_path: Path, yaml_text: str):
    """经 GUI 侧运行器 + 真实 worker 子进程跑一次任务，返回观测结果。"""
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.runner.worker_task_runner import WorkerTaskRunner

    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "provided.yaml"
    cfg_path.write_text(yaml_text, encoding="utf-8")
    config = load_yaml(cfg_path)

    runner = WorkerTaskRunner(project_root=tmp_path)
    logs: list[str] = []
    finished: list[tuple[str, int]] = []
    runner.log_line.connect(lambda text, _level: logs.append(text))
    runner.task_finished.connect(lambda task, code: finished.append((task, code)))

    assert runner.start(config) is True, f"启动失败，日志={logs}"
    pid = runner.get_pid()
    assert pid, "应拿到 worker 子进程 pid"
    assert pid != os.getpid(), "任务必须在独立子进程中执行，而不是当前进程"
    assert psutil.pid_exists(pid), f"worker 子进程 {pid} 应处于存活状态"

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and not finished:
        app.processEvents()
        time.sleep(0.05)

    try:
        assert finished, f"任务未在时限内结束：state={runner.state} 末尾日志={logs[-5:]}"
        assert finished[0][1] == 0, f"退出码应为 0：{finished}"
        assert not any("0 条记录" in text for text in logs), "不应出现零记录告警"
    finally:
        # —— 进程正确退出：不留后台残留 ——
        with contextlib.suppress(Exception):
            runner._backend.shutdown()
        gone_deadline = time.monotonic() + 30
        while time.monotonic() < gone_deadline and psutil.pid_exists(pid):
            app.processEvents()
            time.sleep(0.1)
        runner._poller.stop()
        app.processEvents()
    assert not psutil.pid_exists(pid), f"worker 子进程 {pid} 未退出，存在资源残留"
    return config, logs


def _read_records(workspace: Path) -> list[dict]:
    records_path = workspace / "output" / "records.jsonl"
    assert records_path.is_file(), (
        f"应有产物文件，实际：{sorted(p.name for p in (workspace / 'output').glob('*'))}"
    )
    return [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_gui_run_single_page_delivers_verified_records(tmp_path: Path) -> None:
    """单页列表：恰好交付真值 3 条，字段/来源正确，JSONL + CSV 可见。"""
    workspace = (tmp_path / "ws").resolve()
    with _serve(_SINGLE_PAGE) as base:
        seed = f"{base}/list"
        config, _logs = _drive_gui_worker(
            tmp_path,
            _config_yaml(seed, workspace, source_kind="static_html", max_depth=1, xlsx=False),
        )

    # 走 GUI 加载路径：未建模键（item_selector）应进入 passthrough 并在保存时保真
    assert config.passthrough["extract"]["item_selector"] == "div.item", (
        "GUI 加载后不应改写列表项选择器"
    )

    records = _read_records(workspace)
    got = [(r["data"]["标题"], r["data"]["价格"]) for r in records]
    assert len(got) == len(SINGLE_PAGE_EXPECTED), f"应恰好 3 条（无漏采/额外/重复）：{got}"
    assert set(got) == set(SINGLE_PAGE_EXPECTED), f"真值不符：{got}"
    assert {r["source_url"] for r in records} == {seed}, "每条记录都应带正确的来源 URL"
    assert (workspace / "output" / "records.csv").is_file(), "应同时产出 CSV"


def test_gui_run_two_level_list_to_detail_delivers_and_exports_xlsx(tmp_path: Path) -> None:
    """列表→详情两级：4 页 6 条真值 + 来源覆盖 4 个 URL + XLSX 可重新打开。"""
    workspace = (tmp_path / "ws").resolve()
    with _serve(_TWO_LEVEL) as base:
        seed = f"{base}/list"
        _drive_gui_worker(
            tmp_path,
            _config_yaml(seed, workspace, source_kind="crawl", max_depth=2, xlsx=True),
        )

    records = _read_records(workspace)
    got = [(r["data"]["标题"], r["data"]["价格"]) for r in records]
    assert len(got) == len(TWO_LEVEL_EXPECTED), f"两级应交付 6 条：{got}"
    assert set(got) == set(TWO_LEVEL_EXPECTED), f"真值不符：{got}"

    # 来源必须覆盖列表页与 3 个详情页 —— "列表→详情"真的走了两层，而非只抓首页
    sources = {r["source_url"] for r in records}
    expected_sources = {f"{base}{path}" for path in _TWO_LEVEL}
    assert sources == expected_sources, f"来源应覆盖 4 个页面：got={sources}"

    # XLSX 可重新打开，且内容与真值一致
    workbooks = sorted((workspace / "output").glob("*.xlsx"))
    assert workbooks, "应产出 XLSX"
    import openpyxl

    sheet = openpyxl.load_workbook(workbooks[0], read_only=True).active
    rows = [row for row in sheet.iter_rows(values_only=True)]
    flat = {str(cell) for row in rows for cell in row if cell is not None}
    assert "标题" in flat and "价格" in flat, f"表头应含字段名：{rows[:2]}"
    for title, price in TWO_LEVEL_EXPECTED:
        assert title in flat, f"XLSX 缺少记录 {title}"
        assert price in flat, f"XLSX 缺少价格 {price}"


# ── 用例 3：API → 游标分页 → 增量（走 GUI 路径）────────────────────────────

#: 游标 API 的固定页；第二次同步会把最后一页改成 last-updated。
CURSOR_PAGES_FIRST = {
    "": ({"id": 1, "value": "first"}, "page-2"),
    "page-2": ({"id": 2, "value": "middle"}, "page-3"),
    "page-3": ({"id": 3, "value": "last"}, None),
}


def _make_cursor_handler(state: dict):
    """刻意只读取首个 cursor 值，复现常见服务端分页语义。"""

    class _CursorApi(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            import urllib.parse

            parsed = urllib.parse.urlsplit(self.path)
            cursor = urllib.parse.parse_qs(parsed.query).get("cursor", [""])[0]
            state["hits"].append(self.path)
            item, next_cursor = state["pages"][cursor]
            body = json.dumps({"items": [item], "next": next_cursor}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # noqa: N802
            return

    return _CursorApi


@contextlib.contextmanager
def _serve_cursor_api():
    state = {"hits": [], "pages": dict(CURSOR_PAGES_FIRST)}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_cursor_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()


def _cursor_config_yaml(seed: str, workspace: Path) -> str:
    """REST + 游标分页 + JSON 抽取。mode/item_path 属 B 类透传键。"""
    return f"""project:
  name: cursor-api
  workspace: {workspace.as_posix()}
source:
  kind: rest
  seeds: [{seed}]
  pagination: {{next_path: $.next, parameter: cursor}}
crawl: {{max_pages: 10, max_depth: 5, same_host: true, concurrency: 1}}
http:
  respect_robots: false
  delay_seconds: 0.0
  timeout_seconds: 10
  retries: 0
  allow_private_network: true
extract:
  mode: json
  item_path: $.items[*]
  fields:
    id: {{path: id}}
    value: {{path: value}}
outputs: {{jsonl: true, csv: false, xlsx: false}}
"""


def test_gui_run_cursor_api_paginates_and_is_incremental(tmp_path: Path) -> None:
    """API→游标分页→增量 走 GUI 路径：游标链完整、末页停止、二次仅交付变化。

    这条同时是 JSON 模式的端到端护栏：mode=json / item_path 都是 GUI 不建模的
    透传键，若往返把它们打回 html，本用例会拿不到任何记录。
    """
    workspace = (tmp_path / "ws").resolve()
    with _serve_cursor_api() as (base, state):
        seed = f"{base}/items?scope=all"
        yaml_text = _cursor_config_yaml(seed, workspace)

        config, _logs = _drive_gui_worker(tmp_path, yaml_text)
        assert config.passthrough["extract"]["mode"] == "json", "GUI 往返不应把 JSON 模式打回 html"
        assert config.passthrough["extract"]["item_path"] == "$.items[*]"

        records = _read_records(workspace)
        assert [r["data"] for r in records] == [
            {"id": 1, "value": "first"},
            {"id": 2, "value": "middle"},
            {"id": 3, "value": "last"},
        ], f"首次同步应交付三页各一条：{records}"
        assert [r["source_url"] for r in records] == [
            seed,
            f"{seed}&cursor=page-2",
            f"{seed}&cursor=page-3",
        ], "每条记录应指向它真正的来源页（含游标）"
        chain = ["/items?scope=all", "/items?scope=all&cursor=page-2", "/items?scope=all&cursor=page-3"]
        assert state["hits"] == chain, "游标链应恰好走一遍并在末页停止"
        assert all(hit.count("cursor=") <= 1 for hit in state["hits"]), "不得重复叠加游标"

        # 第二次同步：末页内容变化 —— 游标链仍要完整重建，但只交付变化的那条
        state["hits"].clear()
        state["pages"]["page-3"] = ({"id": 3, "value": "last-updated"}, None)
        _drive_gui_worker(tmp_path, yaml_text)

        assert state["hits"] == chain, "新同步周期必须重建整条游标链"
        changed = _read_records(workspace)
        assert [r["data"] for r in changed] == [{"id": 3, "value": "last-updated"}], (
            f"未变化页不应重复交付，末页变化只交付一次：{changed}"
        )
