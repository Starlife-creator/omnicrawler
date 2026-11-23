"""CLI 的 **agent 可用性**守卫：参数必须有 help，错误必须有定位信息。

## 为什么这个门禁存在（2026-11-23 的真实审计）

`AGENTS.md` 写着「CLI 即 agent 面」—— 但 agent 拿到的唯一线索就是
`--help` 与错误信息。这一轮我用 CLI 完成构建、组件管理、签名、发布，
暴露了两类真实缺陷：

### 一、35% 的选项没有 help

实测（`check_agent_surface.py --json`）：199 个选项里 **71 个 `help=None`**，
其中 `components` 缺得最多 —— 而它是 agent 最常用的命令之一。
agent 看到 `--sha256` 却不知道「用在哪个 action、不匹配会怎样」。

### 二、错误建议与实际原因错配（比没有建议更糟）

`_print_error_hint` 早先对**任何** `PermissionError` 都提示
「请确认目标网站允许自动访问，或检查网络代理设置」。而真实场景绝大多数是
**本地文件不可写**。2026-11-23 我执行 `omnicrawler components list` 得到
`registry.lock` 的 PermissionError，提示却让我去查网络代理 —— 方向完全错。

**错误的建议比没有建议更糟**：它会让人往无关方向排查，且 agent 不会像人
那样「感觉哪里不对」。

## 判据（确定性）

1. **help 覆盖率**：常规选项（非 `argparse.SUPPRESS`、非内部 dispatch）
   必须有非空 `help`。基线冻结在 `tools/agent_surface_baseline.json`，
   **只许降低不许上升** —— 补一处就下调一处，防止回退。
2. **不出现「万能建议」**：错误提示函数里不得存在「不区分异常子类型就给出
   领域建议」的分支。这条以**行为测试**验收（见
   `tests/unit/cli/test_error_hints.py`），本门禁只查结构性红线：
   `_print_error_hint` 里每个 `isinstance` 分支必须给出针对性文本。

## 边界

不验 help 文本的**质量**（写得清不清楚无法机械判断），只验**存在性**与
「基线不上升」。质量靠 code review。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 基线条目：允许缺 help 的「选项」数量上限（只许降低）。
_BASELINE = _ROOT / "tools" / "agent_surface_baseline.json"

#: 这些选项名属于内部/兼容用途，不要求 help。
_INTERNAL_DESTS = frozenset({"command", "help", "version"})


def collect_missing_help() -> list[tuple[str, str]]:
    """返回 `[(命令名, 选项名), ...]`，列出所有缺 help 的选项。"""
    from omnicrawler.cli._main import build_parser

    parser = build_parser()
    missing: list[tuple[str, str]] = []

    def scan(sub: argparse.ArgumentParser, path: str) -> None:
        for action in sub._actions:  # noqa: SLF001 - argparse 无公开遍历 API
            if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
                for name, child in action.choices.items():
                    scan(child, f"{path} {name}".strip())
                continue
            dest = getattr(action, "dest", "") or ""
            if dest in _INTERNAL_DESTS or not dest:
                continue
            # `argparse.SUPPRESS` 的意思是「不出现在 help 里」，不算缺失
            if getattr(action, "help", None) is argparse.SUPPRESS:
                continue
            if getattr(action, "help", None):
                continue
            flag = next((o for o in action.option_strings if o.startswith("--")), dest)
            missing.append((path or "(root)", flag))

    scan(parser, "")
    return missing


def _load_baseline() -> int | None:
    if not _BASELINE.is_file():
        return None
    try:
        return int(json.loads(_BASELINE.read_text(encoding="utf-8")).get("max_missing_help"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CLI agent 可用性守卫（help 覆盖率）")
    parser.add_argument(
        "--update", action="store_true",
        help="把当前缺失数写成新的基线（只能下调；上调会被拒绝）",
    )
    args = parser.parse_args(argv)

    missing = collect_missing_help()
    count = len(missing)
    baseline = _load_baseline()

    if args.update:
        if baseline is not None and count > baseline:
            print(
                f"{BAD_MARK} 拒绝上调基线：当前缺 {count} 个，基线是 {baseline}。\n"
                "本门禁的设计前提是「只许降低」—— 补 help，不要放宽阈值。"
            )
            return 1
        _BASELINE.write_text(
            json.dumps(
                {"max_missing_help": count,
                 "note": "只许降低。补一处 help 就重跑 --update 下调一处。"},
                ensure_ascii=False, indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        print(f"{OK_MARK} 基线已更新：max_missing_help = {count}")
        return 0

    if count == 0:
        print(f"{OK_MARK} 所有选项都有 help")
        return 0

    if baseline is None:
        print(
            f"{BAD_MARK} 有 {count} 个选项缺 help，且没有基线文件。\n"
            "首次建立基线：python tools/gates/check_agent_surface_help.py --update\n"
            "（之后只许降低）"
        )
        return 1

    if count > baseline:
        print(f"{BAD_MARK} 缺 help 的选项从 {baseline} 涨到 {count}：")
        for command, flag in missing[:15]:
            print(f"  - {command}: {flag}")
        if count > 15:
            print(f"  …另有 {count - 15} 个")
        print("\nagent 只能靠 --help 认识命令。请补上 help，不要上调基线。")
        return 1

    print(f"{OK_MARK} 缺 help 的选项 {count} 个（基线 {baseline}，未超标）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
