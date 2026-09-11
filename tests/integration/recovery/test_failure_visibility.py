"""可靠性验收：异常不得伪装成功，且故障可定位、可恢复。

对应 `优化方案.md` §1.2 可靠性升级项——验收方式为「**故障可注入 / 可定位 / 可恢复**的
闭环，而不是仅依赖零散用例」。本文件是这个闭环的验收端：

| 验收项 | 由谁提供 | 本文件如何断言 |
|---|---|---|
| 可注入 | `tests/support/fault_injection.py` | 注入器记录 `calls` / `injected`，先证明注入确实发生 |
| 可定位 | `frontier` + `errors` + 终态 | 断言失败 URL 在两张表与终态上都能查到 |
| 可恢复 | `Pipeline(retry_failed=True)` | 恢复运行真的重取了先前失败的 URL |

**被修的缺陷**：逐请求失败被隔离（正确的健壮性），但隔离之后没有任何聚合反映到终态，
于是「全部抓取失败」也会报告 `succeeded` —— 即「异常伪装成功」。
而 `core/run_state.py` 早已定义 `partial_success`（别名 `completed_with_errors`）、
GUI worker 与 CLI 退出码也都按它处理，只是**流水线从不发出**，整套机制形同死代码。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from omnicrawler.services.application_service import ApplicationService
from omnicrawler.state import StateStore
from tests.support.fault_injection import fail_all, fail_urls, inject_fetch_faults

_PAGES = 4
_LINKS = "".join(f'<a href="/p{i}">{i}</a>' for i in range(1, _PAGES + 1))
_INDEX = f"<html><body><h1>index</h1>{_LINKS}</body></html>".encode()
_ITEM = b"<html><body><h1>detail</h1><p>content</p></body></html>"

_TEMPLATE = Path(__file__).resolve().parents[3] / "configs" / "full_pipeline.yaml"


class _CountingSite(BaseHTTPRequestHandler):
    """记录请求路径：恢复运行是否真的重取了失败 URL，只能由服务端证明。"""

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
    """本机可跑的最小配置：`retries: 0` 让失败在一次尝试后即进入 dead-letter。"""
    if not _TEMPLATE.is_file():
        pytest.skip(f"缺少配置模板: {_TEMPLATE}")
    data = yaml.safe_load(_TEMPLATE.read_text(encoding="utf-8"))
    data["processors"]["pdf"]["enabled"] = False
    data["project"].update(name="failure_visibility", workspace=str(tmp_path / "work"))
    data["source"]["seeds"] = [f"http://127.0.0.1:{site.server_port}/"]
    data["crawl"].update(allow_domains=["127.0.0.1"], max_pages=50, max_depth=4)
    data["http"].update(allow_private_network=True, respect_robots=False, retries=0, delay_seconds=0)
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_clean_run_reports_succeeded(site, tmp_path: Path) -> None:
    """基线：没有故障时终态仍是 succeeded，且没有错误记录。"""
    summary = ApplicationService(_write_config(tmp_path, site)).run()
    assert summary["status"] == "succeeded", summary
    assert int(summary["errors"]) == 0, summary
    assert int(summary["processed"]) >= _PAGES, summary


def test_all_fetch_failures_are_not_reported_as_success(site, tmp_path: Path, monkeypatch) -> None:
    """★ 核心：整轮一个页面都没交付，就绝不能报告 succeeded。"""
    path = _write_config(tmp_path, site)
    faults = inject_fetch_faults(monkeypatch, should_fail=fail_all())

    summary = ApplicationService(path).run()

    assert faults.injected, "注入没有发生——这条测试会在空跑中假通过"
    assert int(summary.get("processed", 0)) == 0, summary
    assert summary["status"] == "failed", f"全部抓取失败却报告 {summary['status']!r}"
    assert int(summary["errors"]) >= 1, "失败必须留下可定位的错误记录"


def test_partial_failures_report_partial_success(site, tmp_path: Path, monkeypatch) -> None:
    """有交付但存在错误记录 → partial_success（即 completed_with_errors）。"""
    path = _write_config(tmp_path, site)
    faults = inject_fetch_faults(monkeypatch, should_fail=fail_urls(f"/p{_PAGES}"))

    summary = ApplicationService(path).run()

    assert faults.injected, "注入没有发生"
    assert int(summary["processed"]) >= 1, summary
    assert int(summary["errors"]) >= 1, summary
    assert summary["status"] == "partial_success", summary


def test_no_work_left_is_success_not_failure(site, tmp_path: Path) -> None:
    """空 frontier（重跑无事可做）不是失败——判据必须区分「没做事」与「做事全失败」。"""
    path = _write_config(tmp_path, site)
    assert ApplicationService(path).run()["status"] == "succeeded"

    again = ApplicationService(path).run(resume=True)
    assert int(again.get("processed", 0)) == 0, again
    assert again["status"] == "succeeded", again


def test_failed_items_are_locatable_and_recoverable(site, tmp_path: Path, monkeypatch) -> None:
    """失败要能定位（frontier + errors + 终态），并且能被 --retry-failed 恢复。"""
    path = _write_config(tmp_path, site)
    target = f"/p{_PAGES}"

    with monkeypatch.context() as scoped:
        inject_fetch_faults(scoped, should_fail=fail_urls(target))
        first = ApplicationService(path).run()
    assert first["status"] == "partial_success", first

    # —— 可定位：失败的 URL 在三处都查得到 ——
    with StateStore(tmp_path / "work" / "state.sqlite3") as state:
        dead = state.rows("SELECT url FROM frontier WHERE status = 'failed'")
        assert any(str(row["url"]).endswith(target) for row in dead), dead
        recorded = state.rows("SELECT url, stage FROM errors")
        assert any(
            str(row["url"] or "").endswith(target) and row["stage"] == "fetch" for row in recorded
        ), recorded

    # —— 可恢复：撤掉注入后重投失败项，本轮应真正重新抓取并回到 succeeded ——
    site.hits.clear()
    second = ApplicationService(path).run(resume=True, retry_failed=True)

    assert second["status"] == "succeeded", second
    assert int(second["errors"]) == 0, second
    assert any(hit.endswith(target) for hit in site.hits), (
        f"恢复运行没有重取 {target}；服务端收到 {site.hits}"
    )
