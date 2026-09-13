"""Deterministic demo site for the manual walkthrough.

Why a script instead of "bring your own target": a walkthrough is only comparable
across rounds if the input is fixed. This serves the same pages with the same
counts every time, so "应有现象" in `docs/MANUAL_WALKTHROUGH.md` can quote exact
numbers, and a later round can tell a real regression from a different sample.

Cross-machine by design (see `docs/CAPABILITY_MATRIX.md`):

* **核心页面只依赖标准库** —— 列表 / 详情 / 翻页 / robots 在"只 clone 了仓库"的机器上
  也能起站点；
* **附件需要 `reportlab`**（`pdf` extra）。缺它时**降级**：站点照常启动、只是不提供
  `/files/contract.pdf`，并由 `attachment_skip_reason()` 给出**补齐命令** ——
  不抛异常、也不假装附件可用。

Usage::

    python tools/walkthrough_demo_site.py --port 8765
    # then use the printed seed URL in the GUI

Pure-stdlib helpers（`build_pages` 之前的部分）可被测试直接调用，不需要 GUI 或网络。
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

LIST_TOTAL = 6
PAGE2_TOTAL = 2
ATTACHMENT_PATH = "/files/contract.pdf"

#: 走查清单与测试共用同一份期望值，避免文档里的数字悄悄漂移。
#: 它是**完整能力环境**下的契约；附件是否可用由 `attachment_available()` 单独反映。
EXPECTED: dict[str, int] = {
    "list_items": LIST_TOTAL,
    "all_items": LIST_TOTAL + PAGE2_TOTAL,
    "attachments": 1,
}

_PAGE1 = tuple((f"示例条目 {i}", str(i * 11)) for i in range(1, LIST_TOTAL + 1))
_PAGE2 = tuple((f"第二页条目 {i}", str(900 + i)) for i in range(1, PAGE2_TOTAL + 1))

#: 附件的取值（也作为走查真值；如需与 `pdfx` 端到端对齐可从这里取）
ATTACHMENT_FIELDS = {"编号": "DEMO-0001", "名称": "走查演示附件", "金额": "1,234.56 元"}


def attachment_available() -> bool:
    """本机能否提供 PDF 附件（取决于 `reportlab` 是否随 `pdf` extra 装上）。"""
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


def attachment_skip_reason() -> str:
    """附件不可用时的**可执行**说明（含补齐命令，而不是只说"不可用"）。"""
    return (
        "附件不可用：缺少 reportlab。补齐："
        'pip install -e ".[pdf]"（或 pip install "reportlab>=4,<6"）'
    )


def _item_html(title: str, price: str, *, index: int) -> str:
    return (
        f'<div class="item">'
        f'<h2 class="title">{title}</h2>'
        f'<span class="price">{price}</span>'
        f'<a class="detail" href="/detail/{index}">详情</a>'
        f"</div>"
    )


def _list_html(rows: tuple[tuple[str, str], ...], *, next_href: str | None) -> str:
    items = "".join(
        _item_html(title, price, index=index + 1) for index, (title, price) in enumerate(rows)
    )
    nav = f'<a rel="next" href="{next_href}">下一页</a>' if next_href else ""
    return f'<html><body><div class="list">{items}</div><nav>{nav}</nav></body></html>'


def _detail_html(title: str, price: str) -> str:
    return (
        "<html><body>"
        f'<div class="detail-view"><h1 class="title">{title}</h1>'
        f'<span class="price">{price}</span>'
        "<p>这是走查用详情页；内容固定，便于跨轮次比较。</p></div>"
        "</body></html>"
    )


def build_attachment_pdf() -> bytes | None:
    """用 reportlab 现造一份中英文 + 表格的 PDF；**缺依赖时返回 None（降级）**。"""
    if not attachment_available():
        return None

    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    buffer = io.BytesIO()
    style = ParagraphStyle("cn", fontName="STSong-Light", fontSize=12, leading=18)
    SimpleDocTemplate(buffer, pagesize=A4).build(
        [
            Paragraph("Walkthrough Attachment / 走查演示附件", style),
            Spacer(1, 6 * mm),
            Paragraph(f"编号：{ATTACHMENT_FIELDS['编号']}", style),
            Paragraph(f"名称：{ATTACHMENT_FIELDS['名称']}", style),
            Paragraph(f"金额：{ATTACHMENT_FIELDS['金额']}", style),
            Spacer(1, 6 * mm),
            Table(
                [["Item / 项目", "Qty / 数量"], ["Consulting / 咨询", "2"]],
                style=TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ]
                ),
            ),
        ]
    )
    return buffer.getvalue()


def build_pages() -> dict[str, tuple[str, bytes]]:
    """返回 ``路径 -> (content-type, body)``；全部在启动时生成，内容固定。"""
    pages: dict[str, tuple[str, bytes]] = {
        "/": ("text/html; charset=utf-8", _list_html(_PAGE1, next_href="/list?page=2").encode()),
        "/list": ("text/html; charset=utf-8", _list_html(_PAGE2, next_href=None).encode()),
        "/robots.txt": ("text/plain; charset=utf-8", b"User-agent: *\nAllow: /\n"),
    }
    attachment = build_attachment_pdf()
    if attachment is not None:
        pages[ATTACHMENT_PATH] = ("application/pdf", attachment)
    for index, (title, price) in enumerate(_PAGE1 + _PAGE2, start=1):
        pages[f"/detail/{index}"] = (
            "text/html; charset=utf-8",
            _detail_html(title, price).encode(),
        )
    return pages


def build_handler(pages: dict[str, tuple[str, bytes]]):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 —— http.server 回调命名约定
            parsed = urlsplit(self.path)
            path = parsed.path
            # `/list?page=2` 与 `/list` 用同一份第 2 页内容（走查只看"能翻到下一页"）
            if path == "/list" and parse_qs(parsed.query).get("page") != ["2"]:
                path = "/"
            entry = pages.get(path)
            if entry is None:
                self.send_error(404)
                return
            content_type, body = entry
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # 静音访问日志
            return

    return _Handler


def build_server(port: int = 0) -> ThreadingHTTPServer:
    """构建（但不启动循环）演示站点；``port=0`` 取随机端口。"""
    return ThreadingHTTPServer(("127.0.0.1", port), build_handler(build_pages()))


def main() -> None:
    parser = argparse.ArgumentParser(description="走查用固定演示站点")
    parser.add_argument("--port", type=int, default=0, help="监听端口（0=随机）")
    args = parser.parse_args()

    server = build_server(args.port)
    seed = f"http://127.0.0.1:{server.server_port}/"
    print(f"演示站点已启动：{seed}")
    print(f"  列表第 1 页 {EXPECTED['list_items']} 条；翻页后合计 {EXPECTED['all_items']} 条")
    if attachment_available():
        print(f"  附件：http://127.0.0.1:{server.server_port}{ATTACHMENT_PATH}")
    else:
        print(f"  {attachment_skip_reason()}")
        print("  （列表 / 详情 / 翻页不受影响；走查可先跳过附件那一步）")
    print("  Ctrl+C 结束")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
