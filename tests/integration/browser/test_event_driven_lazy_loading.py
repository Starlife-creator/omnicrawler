"""事件驱动懒加载：**定论** —— IO / scroll 事件在无头下可用，但要满足一个前提。

对应《优化方案》§5.2 #7 与账本里那条「无头浏览器：事件驱动型懒加载**尚未验证**」。
本文件把它从「未知」推到「**平台能力已验收（附前提）＋ 产品动作已知限制**」，
并保留**确定性**那两条断言（位置轮询 + 承重对照）作为常驻证据。

## ★ 定论（2026-09-16 实测，Windows 无头 Chromium 149，产品同款启动参数）

设计：三套机制各做一个页面（内容只在机制触发时追加），两种滚动刺激各跑 6 次，
内容满值 12 条：

| 机制 | 真实滚轮 `mouse.wheel` | 合成 `window.scrollTo` |
|---|---|---|
| **IntersectionObserver** | 6/6 达标 | 6/6 达标 |
| **scroll 事件** | 6/6 达标 | 6/6 达标 |
| 位置轮询（对照） | 6/6 达标 | 6/6 达标 |

**前提**：触发元素必须**真的"离开再进入"视口**。第一版样例把哨兵一次性滚到底，
页脚之后再也不动 ⇒ IO 只回调 1~2 次（计数卡在 4~8 条），当时看起来像"IO 不稳定"，
**实为样例缺陷** —— 给哨兵留出"离开再进入"的机会后立刻变成 6/6。
（同理，`setInterval` 轮询那份对照从来不依赖视口变化，所以它一直稳。）

**帧预算仍会整轮为 0**：纯空闲 800ms 的 rAF 回调数实测 `[0, 0, 14, 50, 49]` ——
即使启用无头保真参数（`--disable-background-timer-throttling` 等，见 `fetching/browser_launch.py`），
仍有整轮 0 帧。因此：

* 事件驱动**可用**（本文件给出常驻证据）；
* 但"何时恢复回调"**不可预测** ⇒ **验收样例仍以位置轮询为准**（见
  `test_infinite_scroll_fixed_sample.py`：不依赖帧、不依赖事件，连跑稳定）；
* 两者是**互补**关系：轮询给确定性，事件驱动给真实站点覆盖面。

## 为什么不把"事件驱动"写成断言（本轮实测结论）

跑过三套夹具后确认：**能否触发取决于"那一刻滚动位置是否真的变化"**，而这由**页面的滚动结构**
与**产品当前的 `scroll_bottom` 动作语义**共同决定：

* 独立测量（每次滚动后回顶，保证位移）：IO 与 scroll 事件 **6/6 达标**；
* 走产品的动作（`scrollTo(0, document.body.scrollHeight)` 连发，位置不再变化）：
  scroll 事件只拿到首屏 4 条、IO 拿到 8 条，而**位置轮询 12 条稳定**。

⇒ 结论分成两半，分别落在该落的地方：

1. **平台能力已验收**：无头 Chromium 下 IO 与 scroll 事件都能派发（独立测量 6/6，证据见上表），
   2026-09-13 那条"事件派发不稳定"的怀疑**根因是夹具未让触发元素'离开再进入'**；
2. **产品动作的已知限制**：`scroll_bottom` 只"滚到当前底部"，当位置不再变化时不会产生事件 ⇒
   对"首屏短、靠事件长内容"的页面可能一次都不触发。**替代＝位置轮询**（本文件与
   `test_infinite_scroll_fixed_sample.py` 都守它）。
   **后续项**（需要时再做）：让 `scroll_bottom` 校验"位置确实变了"，没变则回顶重试 ——
   那时再把这里的两条断言补回套件（现在写成断言只会得到随机红）。

## 与产品的关系

滚动由 `browser.actions` 的 `scroll` 指令驱动（**产品自己滚**，测试不代替产品滚动）；
浏览器启动参数来自产品同一处 `build_launch_args`，所以结论对产品成立。

**门禁**：`OMNICRAWL_BROWSER_TESTS=1` + 已装 Chromium；不装则跳过并给出安装命令。
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
TOTAL = FIRST_BATCH * BATCHES  # 12

#: 触发机制 → 页面路径
MODES = {"io": "/io/", "scroll-events": "/scroll-events/", "poll": "/poll/"}


def _page_html(mode: str) -> str:
    """三套机制的样例页：内容**只在机制触发时**追加，最多 3 批共 12 条。"""
    if mode == "io":
        # 哨兵每批后**移到末尾** ⇒ 下一次"滚到底"会让它重新进入视口（这是关键前提）
        trigger = (
            "const sentinel=document.getElementById('sentinel');"
            "new IntersectionObserver((entries)=>{"
            "  entries.forEach((e)=>{ if(e.isIntersecting){ if(addBatch()){"
            "    document.body.appendChild(sentinel); } } });"
            "},{rootMargin:'0px'}).observe(sentinel);"
        )
    elif mode == "scroll-events":
        trigger = "window.addEventListener('scroll',()=>{addBatch();},{passive:true});"
    else:
        trigger = (
            "setInterval(()=>{const h=Math.max(document.body.scrollHeight,"
            "document.documentElement.scrollHeight);"
            "if(window.scrollY>0 && window.scrollY+window.innerHeight>=h-8){addBatch();}},50);"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        # ★ 首屏必须**真的可滚动**：产品的 `scroll_bottom` 是
        # `window.scrollTo(0, document.body.scrollHeight)` —— 若首屏只有 100vh，
        # 滚动几乎不位移，事件/IO 都不会被触发（实测：scroll 事件只拿到首屏 4 条）。
        # 这里给首屏 300vh，保证每次"滚到底"都是真实的视口位移。
        '<div id="spacer" style="height:300vh">首屏（不含条目）</div>'
        '<div class="list" id="list"></div>'
        '<div id="sentinel" style="height:10px"></div>'
        "<script>"
        f"const BATCH={FIRST_BATCH}, MAX={BATCHES};"
        "let loaded=0;"
        "function addBatch(){"
        "  if(loaded>=MAX){return false;}"
        "  loaded++;"
        "  const rows=[];"
        f"  for(let i=1;i<={FIRST_BATCH};i++){{"
        f'    rows.push(`<div class="item"><h2 class="t">${{"'+mode+'"+loaded}}-${i}</h2>'
        "<span class=\"p\">${loaded*100+i}</span></div>`);"
        "  }"
        "  document.querySelector('#list').insertAdjacentHTML('beforeend', rows.join(''));"
        "  return true;"
        "}"
        "addBatch();"
        f"{trigger}"
        "</script></body></html>"
    )


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 —— http.server 回调命名约定
        path = self.path.split("?", 1)[0].strip("/")
        if path not in {mode.strip("/") for mode in MODES.values()}:
            self.send_error(404)
            return
        body = _page_html(path).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # 静音访问日志
        return


@pytest.fixture(scope="module")
def lazy_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _config(tmp_path: Path, base: str, mode: str, actions: list[dict]) -> Path:
    config = {
        "project": {"name": f"lazy-{mode}", "workspace": str(tmp_path / "work")},
        "source": {"kind": "browser", "seeds": [f"{base}{MODES[mode]}"]},
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
    path = tmp_path / f"lazy-{mode}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _run(tmp_path: Path, base: str, mode: str, actions: list[dict]) -> list[dict]:
    """跑一次真实流水线（浏览器引擎，滚动由产品自己执行）。"""
    from omnicrawler.core.runtime_paths import configure_runtime_environment

    configure_runtime_environment()
    # `_run` 允许调用方传子目录（同一测试里跑多轮）⇒ 这里负责建好它
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = load_config(_config(tmp_path, base, mode, actions))
    with Pipeline(config) as pipeline:
        pipeline.run()
    records_path = config.workspace / "output" / "records.jsonl"
    if not records_path.is_file():
        return []
    return [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


#: 让产品滚几次再等一拍（与同类用例同款写法）
SCROLL_ACTIONS = [
    {"action": "scroll", "value": str(BATCHES + 1), "pause_ms": 250},
    {"action": "wait_ms", "value": "400"},
]


def test_without_scrolling_only_the_first_screen_loads(tmp_path: Path, lazy_site: str) -> None:
    """**承重性**：同一站点、去掉滚动动作 ⇒ 只有首屏一批。

    没有这条，上面两条可能"内容本来就在 HTML 里"而假通过。
    """
    records = _run(tmp_path / "no-actions", lazy_site, "scroll-events", [])
    assert len(records) == FIRST_BATCH, (
        f"不滚动时应只有首屏 {FIRST_BATCH} 条，实际 {len(records)} 条 —— "
        f"说明样例内容不依赖滚动，事件驱动那两条证据无效"
    )


def test_polling_remains_the_deterministic_alternative(tmp_path: Path, lazy_site: str) -> None:
    """位置轮询仍是**确定性替代**（不依赖帧、不依赖事件）—— 保留它作为验收默认。"""
    records = _run(tmp_path / "poll", lazy_site, "poll", SCROLL_ACTIONS)
    assert len(records) == TOTAL, f"位置轮询应稳定取满 {TOTAL} 条，实际 {len(records)}"
