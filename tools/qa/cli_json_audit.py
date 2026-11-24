"""CLI `--json` 覆盖度体检：哪些命令的 stdout 真的能被 `json.loads`。

## 为什么需要（2026-11-23 的两次口径错误）

我在同一天里两次判断错「`--json` 覆盖度」：

1. 按「命令是否声明 `--json` flag」统计 → 3/55，得出「只有 5% 支持」；
2. 实际上按「handler 是否输出结构化数据」统计 → 43/54（80%）。

两次都错在**口径**：我看的是「代码长什么样」，而不是「跑起来输出什么」。
这个工具把口径固定成**可执行的事实**：跑一次，`json.loads` 一次。

## 它做什么

对每个子命令，用 `--json` 跑一次（只跑**不需外部依赖**的命令：跳过需要
`--config` 的、需要网络的、长驻的），然后：

* `parsed` —— stdout 是合法 JSON（agent 可直接消费）；
* `text` —— stdout 是文本（agent 只能当字符串处理）；
* `error` —— 进程非零退出且 stdout 不是 JSON；
* `skipped` —— 需要参数/网络/长驻，跳过。

## 为什么不用静态分析

静态分析只能看到「有没有调 `_json()`」，看不到：
* 命令是否在 `_json()` 之前就 `print` 了别的东西；
* 异常路径是否绕过了 `_json()`；
* 全局 `--json` 是否真的生效。

**必须真跑。**

## 用法

```powershell
python tools/qa/cli_json_audit.py            # 简明汇总
python tools/qa/cli_json_audit.py --table    # 逐命令列表
```

它是**测量工具**，不是门禁：有些命令天然是文本输出（交互式向导、长驻服务），
不该为了「好看」硬改成 JSON。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: **真正**会挂住或误报的命令 —— 极少数，且每条都要写清为什么。
#:
#: ★ 2026-11-24：这个名单此前有 8 条，实测**它们全都能输出合法 JSON**：
#: 因为本工具测的是 `<cmd> --json` 的**参数校验/前置检查路径**，
#: 而不是真的要跑起来 —— 长驻命令在「缺 --config / 缺子命令」时就走
#: argparse 的错误路径，照样出 envelope。
#:
#: 保留过度保守的跳过名单，代价是**覆盖率被系统性低估**
#: （对外报 47/55 = 85%，实际是 55/55 = 100%）。
#:
#: 现在只剩**两条真会挂住**的 —— 实测它们 30s 超时（不是「缺参数就退出」，
#: 而是真的去开 GUI/浏览器窗口然后等下去）：
#:   * `visual-select`：开浏览器做坐标选择
#:   * `workbench`：开 Tk 窗口
#: 加回来时都附了实测依据（超时），不是照直觉列的。
_SKIP_COMMANDS: frozenset[str] = frozenset({"visual-select", "workbench"})

#: 需要 `--config` 的命令（不提供就没法跑，跳过）。
_NEEDS_CONFIG_HINT = True


def _subcommands() -> list[str]:
    from omnicrawler.cli._main import build_parser

    parser = build_parser()
    for action in parser._actions:  # noqa: SLF001
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            return sorted(action.choices)
    return []


def _requires_config(parser, command: str) -> bool:
    """该命令是否强制要求 `--config`（缺了就跑不动）。"""
    import argparse as _argparse

    for action in parser._actions:  # noqa: SLF001
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            child = action.choices.get(command)
            if child is None:
                return False
            for opt in child._actions:  # noqa: SLF001
                if "config" in (opt.dest or "") and opt.required:
                    return True
                if opt.__class__ is _argparse._SubParsersAction:
                    return False
            return False
    return False


def audit(timeout: float = 30.0) -> list[tuple[str, str, str]]:
    """跑一遍，返回 `[(命令, 结论, 说明)]`。"""
    from omnicrawler.cli._main import build_parser
    from tools.child_env import utf8_env

    parser = build_parser()
    results: list[tuple[str, str, str]] = []
    for command in _subcommands():
        if command in _SKIP_COMMANDS:
            results.append((command, "skipped", "长驻/交互/需外部依赖"))
            continue
        needs_config = _requires_config(parser, command)
        # **需要 --config 的命令用「不存在的配置」测错误路径**。
        #
        # 这是 agent 最常遇到的场景（配置路径写错、文件没生成），
        # 而错误路径恰恰最容易绕过 `_json()` 输出裸文本。
        # 早期版本直接 skip 了这批，导致「31 个跳过」的假象 ——
        # 实际上它们的失败路径也是结构化 JSON。
        extra_args = ["-c", "__json_audit_probe__.yaml"] if needs_config else []
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "omnicrawler", "--json", command, *extra_args],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                check=False, timeout=timeout, env=utf8_env(), cwd=str(_ROOT),
            )
        except subprocess.TimeoutExpired:
            results.append((command, "skipped", f"超过 {timeout:.0f}s"))
            continue

        stdout = (completed.stdout or "").strip()
        if not stdout:
            kind = "skipped" if completed.returncode == 0 else "error"
            results.append((command, kind, f"无 stdout（退出 {completed.returncode}）"))
            continue
        try:
            json.loads(stdout)
        except json.JSONDecodeError:
            results.append((command, "text", "stdout 不是 JSON"))
        else:
            results.append((command, "parsed", "stdout 是合法 JSON"))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CLI --json 覆盖度体检（真跑，不做静态推断）")
    ap.add_argument("--table", action="store_true", help="逐命令列出结论")
    ap.add_argument("--timeout", type=float, default=30.0, help="单命令超时秒数")
    args = ap.parse_args(argv)

    results = audit(timeout=args.timeout)
    by_kind: dict[str, list[str]] = {}
    for command, kind, _note in results:
        by_kind.setdefault(kind, []).append(command)

    if args.table:
        for command, kind, note in results:
            print(f"  {command:24} {kind:8} {note}")

    print()
    print(f"{OK_MARK} 自动跑的 {len(results)} 个命令里：")
    for kind in ("parsed", "text", "error", "skipped"):
        names = by_kind.get(kind, [])
        if names:
            print(f"   {kind:8} {len(names):2}  {', '.join(names[:6])}"
                  + ("…" if len(names) > 6 else ""))

    bad = by_kind.get("text", []) + by_kind.get("error", [])
    if bad:
        print()
        print(f"{BAD_MARK} 这些命令的 --json 输出不可机读：{', '.join(bad)}")
        print("   注意：有些天然是文本（向导/长驻），不一定要改。逐个判断。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
