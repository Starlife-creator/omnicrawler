"""固定动态样例：**滚动加载（内容随滚动出现）** —— 滚动确实带来更多内容、无重复、不依赖公网。

账本里「动态页面→分页/滚动→去重」在补充分页证据后是「已验证（**分页**；滚动加载仍待）」，
本文件补上滚动这一半。

## 样例

单页 `/scroll/`：首屏 **4** 条由内联 JS 注入；此后**只有在页面被滚动过之后**才会继续追加，
每批 4 条、最多 3 批 ⇒ 真值 **12** 条且互不相同。

抓取侧用产品自身的滚动动作（`browser.actions` 的 `scroll` → `scroll_bottom`），
不是"在测试里手动 evaluate" —— 验的是产品真的能靠滚动取到全部内容。

## 为什么用"轮询滚动位置"而不是 scroll 事件 / IntersectionObserver

这是本文件最重要的设计决定，来自实测（2026-09-13，Windows 无头 Chromium）：

* `window.scrollTo` 能改变滚动位置，但 **scroll 事件的派发不稳定** ——
  同一参数、同一动作序列重复跑，事件计数时有时无；
* `IntersectionObserver` 同样不稳定（实测有整轮 0 回调的情形）；
* 根因是**无头下渲染帧预算被压得很低**（纯空闲 800ms 实测 rAF 帧数：
  默认启动 `[1, 7, 12, 7, 29]`；关掉后台/遮挡抑制后 `[13, 43, 14, 38, 11]`）——
  帧少则布局提交与事件派发都会滞后，于是某些 `scrollTo` 被"浪费"。

结论：**验收样例不能建立在 scroll 事件或 IO 之上**，否则测试自身会随机失败。
真实站点两种做法都有，而"轮询滚动位置"确定可重复，故样例采用它。
（若将来要验收"事件驱动的懒加载"，必须先让无头帧预算稳定，那是另一件事。）

## 两个用例的分工

1. `test_..._yields_all_batches`：带滚动动作 ⇒ 12 条、无重复、来源为同一页面；
2. `test_scroll_is_load_bearing`：**同一站点、去掉滚动动作 ⇒ 只有首屏 4 条**。
   这是**常驻回归**（不是一次性人工核对）：一旦样例退化（比如内容不再依赖滚动），
   第二条会直接失败，第一条的证据也就不再成立。

**门禁（与仓库既有浏览器用例同款约定）**：`OMNICRAWL_BROWSER_TESTS=1` + 已装 Chromium。
不装则**跳过**，跳过理由里给出安装命令。
"""

from __future__ import annotations

import json
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

FIRST_BATCH = 4
BATCHES = 3
TOTAL = FIRST_BATCH * BATCHES

#: 视口之外多远仍算"接近底部"。放得比一批内容还宽，使**一次到底**就能把剩余批次连续补上
#: （真实站点的"连续加载"就是这个行为）；同时不影响"完全不滚动 ⇒ 只有首屏"。
_NEAR_BOTTOM_PX = 2000

SCROLL_ACTIONS = [
    {"action": "scroll", "value": str(BATCHES), "pause_ms": 250},
    {"action": "wait_ms", "value": "400"},
]


def _expected_titles() -> set[str]:
    """真值：`scroll-{batch}-{index}`，全部互不相同。"""
    return {
        f"scroll-{batch}-{index}"
        for batch in range(1, BATCHES + 1)
        for index in range(1, FIRST_BATCH + 1)
    }


def _page_html() -> str:
    """静态骨架 + **由内联 JS 注入**的条目；追加以"页面被滚动过"为前提。

    每个 `.item` 的 `min-height` 保证页面确实可滚动（否则 `window.scrollTo`
    不会改变位置，样例会退化成"永远只有首屏"）。
    """
    return (
        "<!doctype html><html><head><style>"
        "html,body{margin:0;padding:0;}"
        ".item{min-height:320px;}"
        "</style></head><body>"
        '<div class="list" id="list"></div>'
        "<script>"
        f"const BATCH={FIRST_BATCH}, MAX_BATCHES={BATCHES};"
        "let loaded=0;"
        "function addBatch(){"
        "  if(loaded>=MAX_BATCHES){return;}"
        "  loaded++;"
        "  const rows=[];"
        "  for(let i=1;i<=BATCH;i++){"
        "    rows.push(`<div class=\"item\"><h2 class=\"t\">scroll-${loaded}-${i}</h2>"
        "<span class=\"p\">${loaded*100+i}</span></div>`);"
        "  }"
        "  document.querySelector('#list').insertAdjacentHTML('beforeend', rows.join(''));"
        "}"
        "addBatch();"
        # 触发条件是"滚动位置"（不是 scroll 事件、也不是 IO）：见模块 docstring 的实测说明。
        "setInterval(()=>{"
        "  const h=Math.max(document.body.scrollHeight,document.documentElement.scrollHeight);"
        f"  if(window.scrollY>0 && window.scrollY+window.innerHeight>=h-{_NEAR_BOTTOM_PX}){{addBatch();}}"
        "},50);"
        "</script>"
        "</body></html>"
    )


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 —— http.server 回调命名约定
        path = self.path.split("?", 1)[0]
        if path != "/scroll/":
            self.send_error(404)
            return
        body = _page_html().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # 静音访问日志
        return


@pytest.fixture(scope="module")
def scroll_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _config(tmp_path: Path, base: str, actions: list[dict]) -> Path:
    config = {
        "project": {"name": "dynamic-scroll", "workspace": str(tmp_path / "work")},
        "source": {"kind": "browser", "seeds": [f"{base}/scroll/"]},
        "crawl": {"max_pages": 1, "max_depth": 1, "same_host": True, "concurrency": 1},
        "browser": {"engine": "playwright", "headless": True, "actions": actions},
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
    path = tmp_path / "scroll.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _run(tmp_path: Path, base: str, actions: list[dict]) -> tuple[dict, list[dict]]:
    """跑一次真实流水线（浏览器引擎）并返回摘要与记录。

    通过 `browser.actions` 给产品**真实**的滚动指令——测试不代替产品做滚动。
    """
    from omnicrawler.core.runtime_paths import configure_runtime_environment

    # 与产品同源地配置可选运行时（会为项目内置 Chromium 设 PLAYWRIGHT_BROWSERS_PATH）
    configure_runtime_environment()

    config = load_config(_config(tmp_path, base, actions))
    with Pipeline(config) as pipeline:
        summary = pipeline.run()

    records_path = config.workspace / "output" / "records.jsonl"
    records: list[dict] = []
    if records_path.is_file():
        records = [
            json.loads(line)
            for line in records_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return summary, records


@pytest.fixture(scope="module")
def scrolled_run(tmp_path_factory, scroll_site):
    """带滚动动作跑一次 —— 应拿到全部 12 条。"""
    tmp_path = tmp_path_factory.mktemp("scroll-with-actions")
    return _run(tmp_path, scroll_site, SCROLL_ACTIONS)


@pytest.fixture(scope="module")
def unscrolled_run(tmp_path_factory, scroll_site):
    """**对照实验**：同一站点、去掉滚动动作 —— 只应拿到首屏 4 条。"""
    tmp_path = tmp_path_factory.mktemp("scroll-no-actions")
    return _run(tmp_path, scroll_site, [])


def test_scroll_yields_all_batches_without_duplicates(scrolled_run) -> None:
    """滚动动作生效：3 批全部取到、12 条、无重复、来源为同一个页面。"""
    summary, records = scrolled_run

    assert summary["status"] == "succeeded", summary
    assert summary["processed"] == 1, f"样例是单页（靠滚动而非翻页取内容）：{summary}"

    titles = [str(record["data"]["标题"]) for record in records]
    assert len(titles) == TOTAL, f"滚动后应为 {BATCHES}×{FIRST_BATCH}={TOTAL} 条：{len(titles)}"
    assert len(set(titles)) == TOTAL, f"不应有重复记录：{sorted(titles)}"
    assert set(titles) == _expected_titles(), "最后一批也必须取到（要一路滚到底）"

    sources = {str(record["source_url"]) for record in records}
    assert len(sources) == 1, f"单页样例：来源应只有 1 个：{sources}"


def test_scroll_is_load_bearing(unscrolled_run) -> None:
    """**承重性**：不滚动就只有首屏那 4 条。

    若这条不再"更少"，说明 12 条并非来自滚动（例如页面把内容一次性吐出），
    那么"滚动加载已验证"就不成立。常驻回归，不靠一次性人工核对。
    """
    summary, records = unscrolled_run

    assert summary["status"] == "succeeded", summary
    titles = [str(record["data"]["标题"]) for record in records]
    assert len(titles) == FIRST_BATCH, (
        f"不滚动应只得到首屏 {FIRST_BATCH} 条（其余只在滚动后追加）：{len(titles)}"
    )
    assert set(titles) == {f"scroll-1-{i}" for i in range(1, FIRST_BATCH + 1)}, titles
    # 与带滚动的真值相比，差的正是"滚动才出现"的那两批
    assert len(_expected_titles()) - len(set(titles)) == TOTAL - FIRST_BATCH


def test_browser_processes_are_reclaimed(scrolled_run) -> None:
    """跑完后不留浏览器残留进程（资源回收）。

    单独一条用例：缺 `psutil` 时只跳过**这一条**，不影响上面的滚动证据。
    """
    psutil = pytest.importorskip("psutil", reason='需要 psutil 核对进程：pip install -e ".[gui]"')

    _summary, _records = scrolled_run
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
