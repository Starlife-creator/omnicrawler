"""Agent 面：把 CLI 契约导出为机器可读（P2-1）。

## 为什么需要它

AI / 脚本要稳定接管 CLI，必须能**在调用前**知道：有哪些命令、每个命令接受什么参数、
退出码有哪些、stdout 是什么形态。此前这些信息只存在于 `--help` 的文本里，机器读不到，
于是 agent 只能靠猜 —— 而猜错的地方恰好是最贵的地方：把 YAML 当 JSON 解析、
把人类摘要当成结果。

## 设计原则（与本项目判据纪律一致）

1. **不猜**：命令的 stdout 形态要么**可验证**（handler 源码里确有 `_json(...)` 调用），
   要么**显式声明**（见 `DECLARED_OUTPUT`）。两者都没有 ⇒ 门禁判红并**点名该命令**。
2. **双向**：解析器里的命令 ⇄ 注册表里的处理函数，缺任一侧都判红
   （对应已记录教训「解析器里有 ≠ 能执行」）。
3. **不许空转**：命令数为 0 即判红 —— 空集合会让所有断言恒真。

## 用法

    python tools/agent_surface.py              # 输出人类可读摘要
    python tools/agent_surface.py --json       # 输出机器可读契约
    python tools/agent_surface.py --check      # 校验（有问题则退出码 1）
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from omnicrawler.cli._handlers import _registry  # noqa: E402,PLC2701
from omnicrawler.cli._main import build_parser  # noqa: E402

SCHEMA = "agent-surface/1"

#: 无法从源码验证 stdout 形态的命令 —— 必须**显式声明**（值, 说明）。
#: 这些多数是「转发型」命令（把参数原样转给子系统）或交互式命令，
#: 形态由子系统 / 交互决定，本项目**不承诺** JSON。
DECLARED_OUTPUT: dict[str, tuple[str, str]] = {
    "auto-analyze": ("text", "转发 intelligent_scraper.main()，形态由子系统决定，不承诺 JSON"),
    "benchmark": ("text", "性能基准报告，人读文本"),
    "c4a-fetch": ("text", "转发 crawl4ai_bridge.main()，结果默认写文件或文本摘要"),
    "gen-templates": ("text", "模板生成清单，人读文本"),
    "import-easyspider": ("yaml", "默认 YAML；`--format json` 时为 JSON（P2-2）"),
    "pdf": ("text", "转发 PDF 子系统（pdfx），形态由子系统决定"),
    "serve": ("none", "长期运行的 HTTP 服务，无 stdout 契约"),
    "stealth-fingerprint": ("text", "默认文本；`--json` 时为 JSON"),
    "visual-select": ("none", "拉起可视化选择器（交互式），无 stdout 契约"),
    "wizard": ("none", "交互式向导，无 stdout 契约"),
    "workbench": ("none", "GUI / 交互工作台，无 stdout 契约"),
}

#: 退出码约定（文档口径；实际退出的码由 observed_exit_codes 从源码静态提取）。
EXIT_CODE_CONVENTION = {
    "0": "成功",
    "1": "运行期失败（结果未达成）",
    "2": "用法或校验失败（参数/配置不合法）",
}


def _handler_emits_json(fn: Any) -> bool:
    """handler 源码里是否确有 `_json(...)` 调用（**可验证**，不是推断）。"""
    try:
        tree = ast.parse(inspect.getsource(fn))
    except (OSError, TypeError, SyntaxError):  # pragma: no cover - 环境异常
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "_json":
                return True
    return False


def _observed_exit_codes(fn: Any) -> list[int]:
    """从 handler 源码里静态提取 SystemExit(<字面量>) 的码值（可验证）。"""
    try:
        tree = ast.parse(inspect.getsource(fn))
    except (OSError, TypeError, SyntaxError):  # pragma: no cover
        return []
    codes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            func = node.exc.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "SystemExit" and node.exc.args:
                arg = node.exc.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                    codes.add(arg.value)
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "SystemExit" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                    codes.add(arg.value)
    return sorted(codes)


def _subcommands(parser: Any) -> list[str]:
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return sorted(action.choices.keys())
    return []


def _options(parser: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for action in parser._actions:  # noqa: SLF001
        if isinstance(
            action,
            (argparse._SubParsersAction, argparse._HelpAction, argparse._VersionAction),  # noqa: SLF001
        ):
            continue
        default = action.default
        out.append(
            {
                "flags": list(action.option_strings),
                "dest": action.dest,
                "required": bool(action.required),
                "default": default if isinstance(default, (str, int, float, bool, type(None))) else None,
                "choices": list(action.choices) if action.choices else None,
                "nargs": action.nargs,
                "help": (action.help or "").strip() or None,
            }
        )
    return out


def collect_parser_commands() -> dict[str, Any]:
    """从 `build_parser()` 里取出顶层命令及其二级子命令、参数。"""
    parser = build_parser()
    result: dict[str, Any] = {}
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for name, sub in action.choices.items():
                result[name] = {"subcommands": _subcommands(sub), "options": _options(sub)}
    return result


def build_surface() -> dict[str, Any]:
    """组装机器可读的 CLI 契约。"""
    from omnicrawler import __version__

    parser_commands = collect_parser_commands()
    commands: list[dict[str, Any]] = []
    for name in sorted(set(parser_commands) | set(_registry)):
        entry = parser_commands.get(name, {})
        handler = _registry.get(name)
        declared = DECLARED_OUTPUT.get(name)
        if declared is not None:
            stdout, note, source = declared[0], declared[1], "declared"
        elif handler is not None and _handler_emits_json(handler):
            stdout, note, source = "json", None, "verified"
        else:
            stdout, note, source = None, None, "unknown"
        commands.append(
            {
                "name": name,
                "in_parser": name in parser_commands,
                "has_handler": handler is not None,
                "handler": f"{handler.__module__}.{handler.__name__}" if handler else None,
                "subcommands": entry.get("subcommands", []),
                "options": entry.get("options", []),
                "stdout": stdout,
                "stdout_source": source,
                "stdout_note": note,
                "observed_exit_codes": _observed_exit_codes(handler) if handler else [],
            }
        )
    return {
        "schema": SCHEMA,
        "app": "omnicrawler",
        "version": __version__,
        "exit_code_convention": EXIT_CODE_CONVENTION,
        "commands": commands,
    }


def validate_surface(surface: dict[str, Any] | None = None) -> list[str]:
    """校验契约是否**完整且自洽**；返回问题列表（空 = 通过）。"""
    surface = surface if surface is not None else build_surface()
    commands = surface.get("commands", [])
    if not commands:
        return [
            "agent-surface: 命令数为 0 —— 契约为空时所有断言恒真，"
            "门禁不能把它当成通过（解析器的顶层子命令未被取到）"
        ]
    issues: list[str] = []
    for command in commands:
        name = command["name"]
        if not command["in_parser"]:
            issues.append(f"{name}: 注册表里有处理函数，但解析器里没有该命令")
        if not command["has_handler"]:
            issues.append(f"{name}: 解析器里有该命令，但注册表里没有处理函数（解析器里有 ≠ 能执行）")
        if command["stdout"] is None:
            issues.append(
                f"{name}: stdout 形态既无法从源码验证、也没有在 DECLARED_OUTPUT 里声明 —— "
                "请补一条显式声明（不要默认它是 JSON）"
            )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 CLI 的机器可读契约（agent 面）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 契约")
    parser.add_argument("--check", action="store_true", help="校验契约完整性（有问题则退出码 1）")
    args = parser.parse_args(argv)

    surface = build_surface()
    if args.check:
        issues = validate_surface(surface)
        if issues:
            print("agent-surface check failed:", file=sys.stderr)
            for issue in issues:
                print(f"- {issue}", file=sys.stderr)
            return 1
        print(f"agent-surface: OK（{len(surface['commands'])} 个命令）")
        return 0
    if args.json:
        print(json.dumps(surface, ensure_ascii=False, indent=2))
        return 0
    print(f"agent-surface {SCHEMA} · omnicrawler {surface['version']} · {len(surface['commands'])} 个命令")
    for command in surface["commands"]:
        print(
            f"  {command['name']:<22} stdout={command['stdout'] or '未定'}"
            f"（{command['stdout_source']}）  exit={command['observed_exit_codes'] or '-'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
