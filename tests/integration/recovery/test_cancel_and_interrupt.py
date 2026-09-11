# ruff: noqa: E402, I001 —— 导入顺序刻意先声明模块级常量与 fixture，再导入被测模块。

"""可靠性验收：取消真实有效、中断可恢复。

与 `test_failure_visibility.py` 同属 §1.2 可靠性升级项的闭环验收，补上另外两条：
**「取消真实有效」**与**「中断可恢复」**（前者只覆盖了「失败 → 恢复」）。

三条被验证的性质，每条都对应一个容易只做一半的实现：

1. **取消真的会停**——不是「标记一下继续跑完」。断言依据：取消后仍有**未完成的记录**，
   且已完成的少于总量。若取消被忽略，这两条会立刻不成立。
2. **取消/中断不丢工作**——已完成的仍是 `done`，没轮到的仍在队列里（`pending`）；
   不留 `in_progress` 孤儿（`prepare_cycle` 在下次运行开头会回收残留，这里直接断言不产生）。
3. **恢复真的能跑完**——`resume=True` 之后**每一条**记录都到达 `done`，
   且不重置已完成的工作（`resume` 正是为此存在）。

取消走的是产品真实的跨进程通道（工作区里的 `run_control.json`，CLI `stop` / GUI 停止按钮同源），
而不是某种测试专用后门。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.runtime.recovery import RecoveryCenter
from omnicrawler.runtime.run_control import RunControl
from omnicrawler.services.application_service import ApplicationService
from omnicrawler.state import StateStore
from tests.support.fault_injection import fail_urls, inject_fetch_faults

_PAGES = 20
_LINKS = "".join(f'<a href="/p{i}">{i}</a>' for i in range(1, _PAGES + 1))
_INDEX = f"<html><body><h1>index</h1>{_LINKS}</body></html>".encode()
_ITEM = b"<html><body><h1>detail</h1><p>content</p></body></html>"
_TOTAL_REQUESTS = _PAGES + 1

_TEMPLATE = Path(__file__).resolve().parents[3] / "configs" / "full_pipeline.yaml"


class _CountingSite(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        body = _INDEX if self.path == "/" else _ITEM
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # noqa: N802
        return


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingSite)
    server.hits = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _write_config(tmp_path: Path, site) -> Path:
    if not _TEMPLATE.is_file():
        pytest.skip(f"缺少配置模板: {_TEMPLATE}")
    data = yaml.safe_load(_TEMPLATE.read_text(encoding="utf-8"))
    data["processors"]["pdf"]["enabled"] = False
    data["project"].update(name="cancel_and_interrupt", workspace=str(tmp_path / "work"))
    data["source"]["seeds"] = [f"http://127.0.0.1:{site.server_port}/"]
    data["crawl"].update(allow_domains=["127.0.0.1"], max_pages=200, max_depth=4)
    data["http"].update(allow_private_network=True, respect_robots=False, retries=0, delay_seconds=0)
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _frontier(state: StateStore) -> dict[str, int]:
    rows = state.rows("SELECT status, COUNT(*) AS n FROM frontier GROUP BY status")
    return {str(row["status"]): int(row["n"]) for row in rows}


def _workspace(tmp_path: Path) -> Path:
    return tmp_path / "work"


def test_stop_actually_cancels_instead_of_finishing(site, tmp_path: Path) -> None:
    """★ 取消必须真的停：停下时仍有未完成的记录，而不是「标记一下继续跑完」。"""
    path = _write_config(tmp_path, site)
    stop_requested = threading.Event()
    control = RunControl(_workspace(tmp_path))

    def on_event(event: str, _details: dict) -> None:
        # 在第一个页面完成时就通过产品的跨进程通道请求停止——确定性，无时序竞争。
        if event == "crawl_progress" and not stop_requested.is_set():
            stop_requested.set()
            control.request_stop()

    summary = ApplicationService(path).run(callback=on_event)

    assert stop_requested.is_set(), "回调没有触发，测试条件不成立"
    assert summary["status"] == "cancelled", summary

    with StateStore(_workspace(tmp_path) / "state.sqlite3") as state:
        counts = _frontier(state)
    done = counts.get("done", 0)
    pending = counts.get("pending", 0)
    assert counts.get("in_progress", 0) == 0, f"取消后留下孤儿: {counts}"
    assert pending >= 1, f"取消应当留下未完成的工作，实际 {counts}"
    assert done < _TOTAL_REQUESTS, f"取消后却跑完了全部 {_TOTAL_REQUESTS} 条: {counts}"

    # 「可恢复」还包括**用户能发现该怎么恢复**：取消是正常终态，但留下大量待处理请求，
    # 恢复中心必须把它识别为「可继续」，否则 recommended_action 会把用户引到别的操作上。
    overview = RecoveryCenter(load_config(path)).overview()
    assert overview["action_previews"]["continue"]["available"] is True, overview["action_previews"]["continue"]
    assert overview["action_previews"]["continue"]["affected"]["pending_requests"] == pending
    assert overview["recommended_action"] == "continue", overview["recommended_action"]


def test_cancelled_run_is_resumable_to_completion(site, tmp_path: Path) -> None:
    """★ 取消不丢工作：resume 之后每一条记录都到达 done。"""
    path = _write_config(tmp_path, site)
    stop_requested = threading.Event()
    control = RunControl(_workspace(tmp_path))

    def on_event(event: str, _details: dict) -> None:
        if event == "crawl_progress" and not stop_requested.is_set():
            stop_requested.set()
            control.request_stop()

    assert ApplicationService(path).run(callback=on_event)["status"] == "cancelled"

    resumed = ApplicationService(path).run(resume=True)

    assert resumed["status"] == "succeeded", resumed
    with StateStore(_workspace(tmp_path) / "state.sqlite3") as state:
        counts = _frontier(state)
    assert counts.get("done", 0) == _TOTAL_REQUESTS, f"恢复没有跑完全部工作: {counts}"
    assert counts.get("pending", 0) == 0 and counts.get("in_progress", 0) == 0, counts


def test_interrupt_is_recorded_as_cancellation(site, tmp_path: Path, monkeypatch) -> None:
    """中断（模拟 Ctrl-C）必须被记为取消，并留下可恢复的状态。"""
    path = _write_config(tmp_path, site)
    # **按 URL 注入**而不是按「第几次调用」：并发池下调用序号与时序相关，
    # 按序号写会让测试时通时不通（实测：单独跑全过、全量套件里必失败）。
    # 选非种子页——种子必须先成功，否则发现不到链接。
    faults = inject_fetch_faults(
        monkeypatch,
        should_fail=fail_urls("/p10"),
        exc_factory=lambda _index, _request: KeyboardInterrupt(),
    )

    with pytest.raises(KeyboardInterrupt):
        ApplicationService(path).run()

    assert faults.injected, "注入没有发生——这条测试会在空跑中假通过"

    with StateStore(_workspace(tmp_path) / "state.sqlite3") as state:
        runs = state.rows("SELECT status FROM runs ORDER BY rowid DESC LIMIT 1")
        counts = _frontier(state)
    assert runs and runs[0]["status"] == "cancelled", runs
    assert RunControl(_workspace(tmp_path)).read().get("stop_requested") is True, (
        "中断后未记录停止请求——运维侧无法据此判断是否需要恢复"
    )
    # 中断不丢工作：完成的不回退，没轮到的仍在队列里。
    assert counts.get("done", 0) >= 1, counts
    assert counts.get("pending", 0) >= 1, counts
    # ★ 被中断时「已提交但尚未开始」的请求，会因为出网已被我们停用而拿不到响应。
    # 那不是这个 URL 被策略拒绝，**不能记成终态 blocked**——resume 不会重试 blocked，
    # 用户会静默丢掉这部分待抓页面（实测踩到过：{'blocked': 1, 'done': 20}）。
    assert counts.get("blocked", 0) == 0, f"取消把待抓页面判成了终态: {counts}"


def test_interrupted_run_is_resumable_to_completion(site, tmp_path: Path, monkeypatch) -> None:
    """★ 中断可恢复：恢复后连「被中断那一刻正在处理」的记录也要被重新处理并完成。"""
    path = _write_config(tmp_path, site)

    with monkeypatch.context() as scoped:
        faults = inject_fetch_faults(
            scoped,
            should_fail=fail_urls("/p3"),
            exc_factory=lambda _index, _request: KeyboardInterrupt(),
        )
        with pytest.raises(KeyboardInterrupt):
            ApplicationService(path).run()
    assert faults.injected, "注入没有发生"

    resumed = ApplicationService(path).run(resume=True)

    assert resumed["status"] == "succeeded", resumed
    with StateStore(_workspace(tmp_path) / "state.sqlite3") as state:
        counts = _frontier(state)
    # prepare_cycle 会把残留的 in_progress 回收为 pending，所以恢复后不应有任何遗留。
    assert counts == {"done": _TOTAL_REQUESTS}, f"恢复后仍有未完成记录: {counts}"


def test_repeated_interrupt_still_finalizes_the_run(site, tmp_path: Path, monkeypatch) -> None:
    """★ 二次 Ctrl-C 是常见操作：收尾期间再收到中断，终态仍必须落库。

    否则 run 会永远停在 `running`——运维侧看到的是「还在跑」，一个会误导人的状态。
    （收尾本身由 `recover_incomplete_runs` 兜底，但那是事后补救；
    终态应当由运行自己负责写对。）
    """
    path = _write_config(tmp_path, site)

    # 让**多个**链接页中断：这样 drain() 收尾时手头仍有失败的在途请求，
    # 收尾本身也会被打断——正是要覆盖的路径。注入按 URL（确定性），且**作用域化**
    # （否则它在后面的恢复运行里仍然生效，表现为测试收尾时冒出幽灵 KeyboardInterrupt）。
    with monkeypatch.context() as scoped:
        faults = inject_fetch_faults(
            scoped,
            should_fail=fail_urls("/p2", "/p4", "/p6", "/p8", "/p10", "/p12"),
            exc_factory=lambda _index, _request: KeyboardInterrupt(),
        )
        with pytest.raises(KeyboardInterrupt):
            ApplicationService(path).run()
    assert faults.injected, "注入没有发生"

    with StateStore(_workspace(tmp_path) / "state.sqlite3") as state:
        runs = state.rows("SELECT status FROM runs ORDER BY rowid DESC LIMIT 1")
    assert runs, "应当留下一条运行记录"
    # 1) 终态必须落库：收尾被二次中断打断，也不许把 run 卡在 running。
    assert runs[0]["status"] == "cancelled", f"收尾被中断打断，run 卡在 {runs[0]['status']!r}"

    # 2) 收尾没能跑完的 in_progress 不是永久丢失：下一次运行的 prepare_cycle 会把它们
    #    退回待处理，因此恢复仍然是完整的。这里用「恢复后全部 done」来证明这一点。
    resumed = ApplicationService(path).run(resume=True)
    assert resumed["status"] == "succeeded", resumed
    with StateStore(_workspace(tmp_path) / "state.sqlite3") as state:
        counts = _frontier(state)
    assert counts == {"done": _TOTAL_REQUESTS}, f"二次中断后有工作永久丢失: {counts}"
