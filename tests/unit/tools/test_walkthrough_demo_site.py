"""走查演示站点的页面契约。

`docs/MANUAL_WALKTHROUGH.md` 的「应有现象」里写死了条数（列表 6 条、翻页后合计 8 条、
附件 1 个）。**这些数字不能漂移** —— 否则走查会拿一个过期的期望值去判断通过与否。
本文件把页面契约锁住，让文档、脚本、测试共用 `EXPECTED` 这一处真值。
"""

from __future__ import annotations

import importlib.util
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMO_PATH = REPO_ROOT / "tools" / "walkthrough_demo_site.py"


def _demo() -> ModuleType:
    """按**路径**加载（`tools/` 不是包，避免依赖 sys.path 顺序）。"""
    spec = importlib.util.spec_from_file_location("walkthrough_demo_site", _DEMO_PATH)
    assert spec and spec.loader, f"无法加载 {_DEMO_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def site():
    demo = _demo()
    server = demo.build_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield demo, base
    finally:
        server.shutdown()
        server.server_close()


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read()


def test_page_contract_matches_expected_counts(site) -> None:
    """页面条数与 `EXPECTED` 一致 —— 文档引用的数字即此处的数字。"""
    demo, base = site

    status, page1 = _get(f"{base}/")
    assert status == 200
    assert page1.count(b'class="item"') == demo.EXPECTED["list_items"], "列表第 1 页条数变了"
    assert b'rel="next"' in page1, "第 1 页应有下一页链接（走查要能翻页）"

    status, page2 = _get(f"{base}/list?page=2")
    assert status == 200
    items2 = page2.count(b'class="item"')
    assert items2 == demo.EXPECTED["all_items"] - demo.EXPECTED["list_items"], "第 2 页条数变了"

    status, robots = _get(f"{base}/robots.txt")
    assert status == 200 and b"Allow: /" in robots, "robots 应放行，避免干扰走查"


def test_detail_pages_and_attachment_are_served(site) -> None:
    """详情页可打开；附件是一份**真 PDF**（走查要能试附件链路）。"""
    demo, base = site

    status, detail = _get(f"{base}/detail/1")
    assert status == 200 and b'class="title"' in detail, "详情页应可打开并有标题"

    status, attachment = _get(f"{base}{demo.ATTACHMENT_PATH}")
    assert status == 200
    assert attachment[:4] == b"%PDF", "附件必须是真 PDF（否则走查的附件环节没有意义）"
    assert len(attachment) > 1024, f"附件过小，可能生成失败：{len(attachment)} 字节"
    assert demo.EXPECTED["attachments"] == 1


def test_unknown_path_returns_404(site) -> None:
    """未知路径必须 404 —— 走查的"异常态"环节依赖它可控。"""
    _demo_module, base = site

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(f"{base}/not-a-real-page")

    assert exc.value.code == 404
