"""W3.5 作者旅程守卫：把「按 `docs/AUTHOR_GUIDE.md` 实测出的三处缺口」钉成会真失败的判据。

## 为什么要这个文件

W3.5 的做法是**严格照公开文档走一遍**（不读源码、只当作者）。实测出三处问题，每一处
都**不是**靠"文档自述"能发现的，只能靠**照做**才发现：

1. `plugins --help` 的「子命令」散文枚举**漏掉 `scaffold-contract2`** —— 而指南第 1 步
   就是敲这个命令。作者读 help 会以为它不存在（同一份 help 里却列着它的 `--plugin-id`
   参数，自相矛盾）。此类漂移在顶层已有守卫（`test_cli_entry_consistency.py` 的
   "注册表 ↔ 子命令集双向一致"），**但 `plugins` 这一层的散文枚举没有对应守卫**。

2. 脚手架把工程生成到 `<output-dir>/<plugin-id>/`，而它自己打印的 `next` 提示写
   `--local .` ⇒ 与自己的 `plugin_dir` 字段**互相矛盾**；照抄会审到**父目录**，
   只因 `audit` 会递归发现嵌套插件才"碰巧能用" —— 偶然正确不等于正确。

3. ★ 最危险的一处（**空转绿灯**）：生成的契约套件继承共享基类 `Contract2Suite`，而
   `plugin_contract` 标记原本打在**基类所在的模块**上（模块级 `pytestmark` **不随继承**）
   ⇒ 指南第 3 步的 `pytest -m plugin_contract` **一个用例都不选中**，实测输出
   `7 deselected`、**退出码 0** —— 作者会把它当成"没失败＝通过"。

第 3 条是本文件存在的主要理由：**"选中 0 个用例" 必须判红**，否则门禁会长期空转。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from omnicrawler.cli._main import build_parser

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HANDLERS = _REPO_ROOT / "src" / "omnicrawler" / "cli" / "_handlers.py"


def _authoritative_subcommands() -> set[str]:
    """从 `_run_plugins` 的**实际分派**推出权威子命令集（不看文档自述）。"""
    source = _HANDLERS.read_text(encoding="utf-8")
    start = source.index("def _run_plugins(")
    end = source.index('@_register("templates")', start)
    return set(re.findall(r'command == "([^"]+)"', source[start:end]))


def _plugins_help_text() -> str:
    parser = build_parser()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        with pytest.raises(SystemExit):
            parser.parse_args(["plugins", "--help"])
    return buffer.getvalue()


def test_plugins_help_enumerates_every_dispatched_subcommand() -> None:
    """`plugins --help` 必须列出**全部**确实存在的子命令（W3.5 缺口 ①）。"""
    subcommands = _authoritative_subcommands()
    assert subcommands, (
        "没能从 _run_plugins 里读出任何子命令 ⇒ 本守卫的解析逻辑已失效（在空转），"
        "请先修 _authoritative_subcommands()，而不是放宽断言。"
    )
    help_text = _plugins_help_text()
    missing = sorted(name for name in subcommands if name not in help_text)
    assert not missing, (
        f"`plugins --help` 没列出这些**确实存在**的子命令：{missing} ⇒ 作者读 help 会以为"
        f"它们不存在。W3.5 实测：指南第 1 步的 `scaffold-contract2` 就是这样被漏掉的"
        f"（而同一份 help 里却列着它的 `--plugin-id` 参数）。\n--- help ---\n{help_text}"
    )


def test_documented_contract_command_actually_selects_cases(tmp_path: Path) -> None:
    """★ 指南第 3 步 `pytest -m plugin_contract` 必须**真选中**用例（W3.5 缺口 ③，空转绿灯）。

    回归的是：标记只打在基类**模块**上 ⇒ 作者继承来的用例不带标记 ⇒ 实测 `7 deselected`
    且**退出码 0**。所以这里**不能**只看退出码，必须断言"收集到的用例数 ≥ 1"。
    """
    from omnicrawler.plugins.plugin_sdk import scaffold_contract2

    root = scaffold_contract2(tmp_path, "scaff_guard")
    result = subprocess.run(  # noqa: S603 - argv 全为字面量 + sys.executable，无 shell
        [
            sys.executable, "-m", "pytest", "-m", "plugin_contract",
            "--collect-only", "-q", "-p", "no:cacheprovider",
        ],
        cwd=root, capture_output=True, text=True, timeout=300,
    )
    output = result.stdout + result.stderr
    match = re.search(r"(\d+) tests? collected", output)
    assert match, f"没能从输出里解析出收集数量（格式变了？）：\n{output}"
    collected = int(match.group(1))
    assert collected >= 1, (
        f"`pytest -m plugin_contract` **选中 0 个用例** ⇒ 作者会当成「没失败＝通过」"
        f"（这正是 W3.5 实测到的 7 deselected / 退出码 0）。\n--- 输出 ---\n{output}"
    )


def test_scaffold_next_hint_is_followable_as_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """脚手架打印的 `next` 提示必须**照着就能用**（W3.5 缺口 ②）。

    判据：按提示里的 `cd` 切换目录后，用同一提示里的 `--local <target>` 去解析，
    必须落到**含 `plugin.py` 的那个目录**（而不是父目录）。
    """
    from omnicrawler.cli._handlers import _run_plugins  # noqa: SLF001 - 直接测分派行为

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        plugins_command="scaffold-contract2",
        plugin_id="scaff_guard",
        display_name="守卫",
        output_dir=".",
        config=None,
        local=None,
        report=False,
        export_egress=None,
        review=None,
    )
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        with pytest.raises(SystemExit):
            _run_plugins(args)
    payload = json.loads(buffer.getvalue())
    assert payload["ok"] is True, payload

    plugin_dir = (tmp_path / payload["plugin_dir"]).resolve()
    assert (plugin_dir / "plugin.py").is_file(), payload

    steps = [step.strip() for step in payload["next"]]
    cd_steps = [step for step in steps if step.startswith("cd ")]
    audit_steps = [step for step in steps if "audit" in step]
    assert audit_steps, f"next 提示里没有 audit 步骤：{steps}"

    cwd = tmp_path
    if cd_steps:
        cwd = (tmp_path / cd_steps[0][len("cd "):].strip()).resolve()
    local_match = re.search(r"--local\s+(\S+)", audit_steps[0])
    assert local_match, f"audit 步骤里没有 --local 目标：{audit_steps[0]}"
    target = Path(local_match.group(1))
    resolved = target.resolve() if target.is_absolute() else (cwd / target).resolve()
    assert (resolved / "plugin.py").is_file(), (
        f"照 `next` 逐条执行时，audit 会审到 {resolved}（那里没有 plugin.py）⇒ "
        f"提示与自己的 `plugin_dir` 字段互相矛盾（W3.5 实测）。\n--- next ---\n{steps}"
    )
