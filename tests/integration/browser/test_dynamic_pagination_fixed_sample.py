"""固定动态样例：**JS 渲染的分页列表** —— 首末页齐全、总数对得上、无重复、浏览器回收。

账本里「动态页面→分页/滚动→去重」此前是**部分验证**：只有单元回归覆盖"浏览器源能发现链接"
与"子请求继承渲染"，**缺一个不依赖公网的固定真值任务**。本文件补这一环：

* 样例**程序自造、内容固定**：3 页 × 4 条，条目由页面**内联 JS 注入**
  （所以只有真渲染才看得到），页面间用静态 `<a rel="next">` 相连。真值 = 12 条且各不相同。
* 它同时是两处修复的**端到端护栏**：
  - 若 `browser` 源不参与链接发现 ⇒ 只抓第 1 页（`processed == 1`）；
  - 若子请求不继承 `render` ⇒ 第 2/3 页拿不到 JS 注入的内容（记录数不足）。

**门禁（与仓库既有浏览器用例同款约定）**：`OMNICRAWL_BROWSER_TESTS=1` + 已装 Chromium。
这正合"由开发者自行决定是否装浏览器"：不装则**跳过**，跳过理由里给出安装命令。
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline

pytestmark = pytest.mark.skipif(
    os.environ.get("OMNICRAWL_BROWSER_TESTS") != "1",
    reason=(
        "需要真实 Chromium。启用：python -m playwright install chromium，"
        "再设 OMNICRAWL_BROWSER_TESTS=1（或直接用 setup_*.bat / setup_linux.sh / setup_macos.command）"
    ),
)

PAGES = 3
PER_PAGE = 4
TOTAL = PAGES * PER_PAGE


def _expected_titles() -> set[str]:
    """真值：`{page}-{index}` 形式的标题，全部互不相同。"""
    return {f"item-{page}-{index}" for page in range(1, PAGES + 1) for index in range(1, PER_PAGE + 1)}


def _page_html(page: int) -> str:
    """第 `page` 页：静态骨架 + 静态"下一页"链接 + **由内联 JS 注入**的条目。"""
    rows = ",".join(
        f'{{title:"item-{page}-{index}",price:"{page * 100 + index}"}}'
        for index in range(1, PER_PAGE + 1)
    )
    next_link = (
        f'<a rel="next" href="/js/page/{page + 1}/">下一页</a>' if page < PAGES else ""
    )
    return (
        "<!doctype html><html><body>"
        '<div class="list" id="list"></div>'
        f"<nav>{next_link}</nav>"
        "<script>"
        f"const rows=[{rows}];"
        "document.querySelector('#list').innerHTML = rows.map(r =>"
        "  `<div class=\"item\"><h2 class=\"t\">${r.title}</h2>"
        "<span class=\"p\">${r.price}</span></div>`).join('');"
        "</script>"
        "</body></html>"
    )


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 —— http.server 回调命名约定
        path = self.path.split("?", 1)[0]
        page = 1
        if path.startswith("/js/page/"):
            try:
                page = int(path[len("/js/page/") :].strip("/").split("/")[0])
            except ValueError:
                page = 0
        elif path != "/js/":
            self.send_error(404)
            return
        if not 1 <= page <= PAGES:
            self.send_error(404)
            return
        body = _page_html(page).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # 静音访问日志
        return


@pytest.fixture(scope="module")
def dynamic_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _config(tmp_path: Path, base: str) -> Path:
    config = {
        "project": {"name": "dynamic-pagination", "workspace": str(tmp_path / "work")},
        "source": {"kind": "browser", "seeds": [f"{base}/js/"]},
        "crawl": {"max_pages": 10, "max_depth": 2, "same_host": True, "concurrency": 1},
        "http": {
            "respect_robots": False,
            "allow_private_network": True,
            "delay_seconds": 0,
            "retries": 0,
        },
        "extract": {
            "mode": "html",
            "item_selector": "div.item",
            "fields": {"标题": {"selector": "h2.t"}, "价格": {"selector": "span.p"}},
        },
        "outputs": {"jsonl": True, "csv": False, "xlsx": False},
    }
    path = tmp_path / "dynamic.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def completed_run(tmp_path_factory, dynamic_site):
    """跑一次真实流水线（浏览器引擎）并返回摘要、记录与产物路径。"""
    from omnicrawler.core.runtime_paths import configure_runtime_environment

    # 与产品同源地配置可选运行时（会为项目内置 Chromium 设 PLAYWRIGHT_BROWSERS_PATH）
    configure_runtime_environment()

    tmp_path = tmp_path_factory.mktemp("dynamic")
    config = load_config(_config(tmp_path, dynamic_site))
    with Pipeline(config) as pipeline:
        summary = pipeline.run()

    records_path = config.workspace / "output" / "records.jsonl"
    records = []
    if records_path.is_file():
        import json

        records = [
            json.loads(line)
            for line in records_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return summary, records, config.workspace


def test_first_and_last_page_are_delivered_without_duplicates(completed_run) -> None:
    """三页都抓到、总数正确、无重复、来源覆盖首页与末页。"""
    summary, records, _workspace = completed_run

    assert summary["status"] == "succeeded", summary
    assert summary["processed"] == PAGES, f"应恰好抓 3 页（含末页）：{summary}"

    titles = [str(record["data"]["标题"]) for record in records]
    assert len(titles) == TOTAL, f"应为 3×{PER_PAGE}={TOTAL} 条：{len(titles)}"
    assert len(set(titles)) == TOTAL, f"不应有重复记录：{titles}"
    assert set(titles) == _expected_titles(), "首末页内容都应齐全"

    sources = {str(record["source_url"]) for record in records}
    assert len(sources) == PAGES, f"来源应覆盖 3 个页面：{sources}"
    assert sum("page/3" in url for url in sources) == 1, "末页必须被抓到"


def test_browser_processes_are_reclaimed(completed_run, tmp_path_factory) -> None:
    """跑完后不留浏览器残留进程（资源回收）。

    单独一条用例：缺 `psutil` 时只跳过**这一条**，不影响上面的分页证据。
    """
    psutil = pytest.importorskip("psutil", reason='需要 psutil 核对进程：pip install -e ".[gui]"')

    _summary, _records, workspace = completed_run
    leftovers: list[str] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        lowered = cmdline.lower()
        # 只关心"由本项目运行时目录启动"的浏览器：避免误报开发者其它浏览器
        if ("chromium" in lowered or "chrome" in lowered) and (
            "ms-playwright" in lowered or ".runtime" in lowered
        ):
            leftovers.append(f"{proc.info['pid']}: {cmdline[:120]}")

    assert not leftovers, f"运行结束后仍有浏览器进程未回收：{leftovers}"
    assert workspace.is_dir()
