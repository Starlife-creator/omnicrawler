"""Ensure documented top-level CLI commands exist in the actual parser."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from omnicrawler.cli import build_parser

COMMAND = re.compile(r"\bomnicrawler[ \t]+([a-z][a-z0-9-]*)\b")
INVOCATION = re.compile(r"\bomnicrawler[ \t]+([a-z][a-z0-9-]*)([^\r\n`]*)")
OPTION = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*|-[A-Za-z])\b")
# 声明即承诺：DEFAULT_DOCS 里的文件**必须存在**（下方 check_docs 对缺失文件
# 直接记为 issue，fail-closed）。曾把 docs/USER_GUIDE.md 与
# docs/USER_GUIDE_2.0.md 列在此处而两文件均不存在，循环体却 `continue`
# 静默跳过——"目标不存在"被当成"目标通过"，5 份声明实际只查了 3 份
# （审查报告 S44）。
DEFAULT_DOCS = (
    "README.md",
    "docs/INSTALLATION.md",
    "docs/FAQ.md",
    "docs/AGENT_GUIDE.md",
)


def cli_commands(parser: argparse.ArgumentParser | None = None) -> set[str]:
    parser = parser or build_parser()
    commands = {"pdf", "pdf-process", "pdf-extract"}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            commands.update(action.choices)
    return commands


def documented_commands(text: str) -> set[str]:
    return set(COMMAND.findall(text))


def cli_contracts(parser: argparse.ArgumentParser | None = None) -> dict[str, dict[str, set[str]]]:
    parser = parser or build_parser()
    contracts: dict[str, dict[str, set[str]]] = {}
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for command, command_parser in action.choices.items():
            if command == "pdf":
                # 迁移后（2026-11-17）：文档工作台已迁成插件 document-workbench，
                # 宿主侧不再有 `omnicrawler.pdfx`。这里**不再展开** pdf 的子选项：
                # 插件未安装时展不开；即便装了，宿主的文档门禁也不该依赖
                # 插件内部结构（那是插件的真源，不是宿主的）。
                #
                # 仍保留 `contracts["pdf"]` 条目——`pdf` 命令本身还在宿主 CLI 里，
                # 只是调用时才提示安装插件。
                pass
            options = {
                option
                for item in command_parser._actions
                for option in item.option_strings
            }
            subcommands: set[str] = set()
            for item in command_parser._actions:
                if isinstance(item, argparse._SubParsersAction):
                    subcommands.update(item.choices)
                    for nested in item.choices.values():
                        options.update(option for nested_item in nested._actions for option in nested_item.option_strings)
            contracts[command] = {"options": options, "subcommands": subcommands}
    return contracts


def documented_invocations(text: str) -> list[tuple[str, str]]:
    return [(match.group(1), match.group(2)) for match in INVOCATION.finditer(text)]


def check_docs(project_root: Path, paths: tuple[str, ...] = DEFAULT_DOCS) -> list[str]:
    available = cli_commands()
    contracts = cli_contracts()
    issues: list[str] = []
    candidates = [(relative, project_root / relative) for relative in paths]
    candidates.extend((path.name, path) for path in sorted(project_root.glob("OmniCrawler-*-Quick-Start.md")))
    release_guide = project_root / "OmniCrawler-用户指南.md"
    if release_guide.is_file():
        candidates.append((str(release_guide), release_guide))
    for label, path in candidates:
        if not path.is_file():
            # fail-closed：声明检查的文档不存在 = 检查范围缩水，必须显式报错
            issues.append(f"{label}: declared documentation file does not exist: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        for command in sorted(documented_commands(text) - available):
            issues.append(f"{label}: documented command does not exist: omnicrawler {command}")
        for command, remainder in documented_invocations(text):
            contract = contracts.get(command)
            if contract is None:
                continue
            for option in sorted(set(OPTION.findall(remainder)) - contract["options"]):
                issues.append(f"{label}: omnicrawler {command} documents unsupported option {option}")
            if contract["subcommands"]:
                if command == "pdf":
                    # The forwarding entry accepts the PDF parser's global
                    # config option before its stage; the path is not a stage.
                    remainder = re.sub(r'''--config(?:=|\s+)(?:"[^"]*"|'[^']*'|[^\s]+)''', "", remainder)
                words = re.findall(r"(?<![-\w])[a-z][a-z0-9-]*", remainder)
                candidate = next((word for word in words if not word.startswith("http")), "")
                if candidate and candidate not in contract["subcommands"]:
                    issues.append(f"{label}: omnicrawler {command} documents unsupported subcommand {candidate}")
    return issues


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    issues = check_docs(project_root)
    if issues:
        print("CLI documentation consistency check failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("CLI documentation consistency check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
