"""PDF 子系统域：`pdf`。

## 为什么要有这个 section

`pdf` / `pdf-process` / `pdf-extract` 三个 PDF 入口此前**只靠 `argv[0]` 嗅探**分发
（`cli/_main.py` 在 `build_parser` **之前**拦截），后果有三个：

* `omnicrawler --help` **看不到** PDF 能力，用户不知道它存在；
* `tools/check_cli_docs.py` 的契约校验也看不到它（`cli_contracts` 只遍历真子命令），
  于是文档与实现的选项漂移不会被发现；
* 命令清单有两个真源（`build_parser` + 嗅探集合），新增 PDF 能力容易只改一处。

现在 `pdf` 是真子命令，与其它域一视同仁。`pdf-process` / `pdf-extract` 作为
**已发布的 console script** 继续可用（见 `_handlers` 的说明），但它们不会再出现在
`omnicrawler --help` 里——这是刻意的：它们是别名，不是并列的两套命令。

## 参数转发

PDF 子系统有自己的完整 parser（`omnicrawler.pdfx.cli`），这里只做**原样转发**，
不复制它的选项定义（复制就是第二个真源，必然漂移）。因此本 section 只声明一个
`REMAINDER` 位置参数与 `-h/--help`。文档里 `omnicrawler pdf` 之后写选项时要注意：
它们必须属于 PDF 子系统的选项，本文件的契约只覆盖到转发这一层。
"""

from __future__ import annotations

import argparse

#: 参数**转发型**子命令：它们把 `-h/--help` 声明为普通开关并转发给被包装的子系统，
#: 因此 `parser.parse_args([<cmd>, "--help"])` 不会触发 argparse 的 SystemExit。
#: 契约测试（`tests/unit/cli/test_cli_contract.py`）据此区分两类命令——
#: 用显式常量而不是在测试里写死字符串，新增转发型命令时改一处即可。
FORWARDING_COMMANDS = frozenset({"pdf"})


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # add_help=False：`-h/--help` 由下面的显式声明接管，并转发给 PDF 子系统，
    # 这样 `omnicrawler pdf --help` 看到的是子系统真正的帮助，而不是这层薄包装。
    parser = sub.add_parser(
        "pdf",
        help="PDF 子系统：解析、OCR、字段抽取、导出与人工复核（等价 pdfx 命令）",
        add_help=False,
    )
    parser.add_argument(
        "-h", "--help",
        dest="pdf_help",
        action="store_true",
        help="显示 PDF 子系统帮助（等价于 pdfx --help）",
    )
    parser.add_argument(
        "pdf_args",
        nargs=argparse.REMAINDER,
        help="原样转发给 PDF 子系统的参数",
    )
    parser.set_defaults(command="pdf")
