"""`omnicrawler pdf` 的参数转发契约。

**为什么需要这组测试**：`pdf` 是**转发型**子命令（把 `pdf` 之后的 token 原样交给
PDF 子系统），而它的参数在 argparse 层是 `REMAINDER` —— REMAINDER **不捕获以选项
开头的参数**，偏偏 PDF 子系统的 `--config` 必须位于其子命令**之前**。于是
`omnicrawler pdf --config X doctor` 曾直接在顶层报 `unrecognized arguments: --config`，
**自定义 PDF 项目从顶层 CLI 完全不可达**（只能用内置模板）。

此处锁三条不变量：
1. `pdf --config X <stage>` 能把配置真正转发到子系统（**执行路径**）；
2. 不带 `--config` 时仍走内置模板（不能为修 1 而破坏默认行为）；
3. `pdf` 仍是**注册的真子命令**（`--help` 与 CLI 文档契约要看得见）。
   修复只切分执行路径，不得让发现路径退化。

**可用形态与边界**（刻意取舍）：预切分只在 `pdf` 是**首个 token** 时生效，因此
- ✅ `omnicrawler pdf --config X <stage>`（推荐形态，本文件锁住）；
- ✅ `pdfx --config X <stage>`（console script，最无歧义）；
- ⚠️ `omnicrawler --log-level DEBUG pdf --config X <stage>`：此时 `pdf` 不是首个 token，
  会走注册子命令那条路，仍受 REMAINDER 限制而报错。**不为它复制一套 pdfx 的选项知识**
  （那就是"第二个真源"）；父命令的 usage 已经把 `pdf` 是可用的子命令显示出来了。
   修复只切分执行路径，不得让发现路径退化。
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from omnicrawler.cli._main import build_parser, main

_MINIMAL_CONFIG = """project_name: forwarding-probe
input_dir: {root}/in
work_dir: {root}/work
output_dir: {root}/out
database: {root}/work/pipeline.sqlite3

parser:
  workers: 1

ocr:
  backend: none

fields:
  - name: probe
    label: 探测字段
    type: text
    source: content
    patterns:
      - '探测\\s*[：:]\\s*(?P<value>.+)'
"""


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "pdf.yaml"
    path.write_text(_MINIMAL_CONFIG.format(root=tmp_path.as_posix()), encoding="utf-8")
    return path


def _json_of(out: str) -> dict:
    """取出阶段结果的 JSON（stdout 可能混有非 JSON 行）。

    ★ 别用"路径子串是否出现在 stdout 里"来断言：JSON 会把反斜杠转义成两个，
    子串匹配会假失败（本文件第一版就踩了这个坑）。
    """
    start, end = out.find("{"), out.rfind("}")
    assert start >= 0 and end > start, f"应输出 JSON：{out[:400]}"
    return json.loads(out[start : end + 1])


def _run(argv: list[str]) -> str:
    """跑一次 CLI 并把 stdout 收成字符串（pdfx 的阶段结果以 JSON 打到 stdout）。

    CLI 正常结束也会 `SystemExit`（`raise SystemExit(code)`），故此处接住；
    退出码非 0 才算失败，避免把"正常退出"误当测试错误。
    """
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            main(argv)
        except SystemExit as exc:
            assert not exc.code, f"CLI 退出码非 0：{exc.code}"
    return buf.getvalue()


def test_pdf_forwards_config_placed_before_subcommand(tmp_path: Path) -> None:
    """`pdf --config X doctor`：配置必须真的到达子系统（这是修复前做不到的）。"""
    config = _config(tmp_path)

    out = _run(["pdf", "--config", str(config), "doctor"])

    payload = _json_of(out)
    assert payload["stage"] == "doctor", payload
    assert Path(payload["result"]["config"]) == config, payload["result"]["config"]


def test_pdf_without_config_still_uses_builtin_template() -> None:
    """不带 `--config` 时仍用内置模板 —— 修转发不能破坏默认行为。"""
    out = _run(["pdf", "doctor"])

    payload = _json_of(out)
    assert payload["stage"] == "doctor", payload
    assert Path(payload["result"]["config"]).name == "generic_template.yaml", (
        payload["result"]["config"]
    )


def test_pdf_remains_a_registered_subcommand() -> None:
    """`pdf` 仍是注册的真子命令 —— 发现路径（`--help` / CLI 文档契约）不得退化。

    执行路径改为预切分后，这一点容易被顺手删掉，故单独锁住。
    """
    args = build_parser().parse_args(["pdf"])

    assert args.command == "pdf", args
    assert getattr(args, "pdf_args", None) == [], "pdf 之后无参数时应为空转发列表"
