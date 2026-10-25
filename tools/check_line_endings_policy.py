#!/usr/bin/env python3
"""行尾（换行）规则一致性门禁（C-#23）。

历史问题：``AGENTS.md`` 曾表述「本仓主仓文件是 CRLF」，与 ``.gitattributes``
现行规则（文本默认 LF，仅 ``*.bat`` / ``*.cmd`` 检出转 CRLF，``*.ps1`` /
``*.sh`` / ``*.command`` 钉死 LF）矛盾，新贡献者据此手工处理换行时会引入
``\\r`` 故障复现。

本脚本做两件确定性校验：

1. ``.gitattributes`` 必须包含现行规则（兜底 LF + 各类脚本钉死行尾）；
2. 开发纪律文档（``AGENTS.md``、``docs/CODING_STANDARDS.md``）不得残留
   「主仓文件是 CRLF」之类的过期表述，且 ``AGENTS.md`` 必须把
   ``.gitattributes`` 指明为准据。

用法::

    python tools/check_line_endings_policy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 参与过期表述扫描的纪律文档（相对仓库根）。
DISCIPLINE_DOCS = ("AGENTS.md", "docs/CODING_STANDARDS.md")

#: 过期表述：与 .gitattributes 现行规则直接冲突的旧说法（出现即判红）。
STALE_CLAIMS = ("本仓主仓文件是 CRLF", "主仓文件都是 CRLF", "全部使用 CRLF")


def _rule_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _has_rule(rules: list[str], pattern: str, eol: str) -> bool:
    for rule in rules:
        if rule.startswith(pattern) and f"eol={eol}" in rule:
            return True
    return False


def check(root: Path = PROJECT_ROOT) -> list[str]:
    issues: list[str] = []

    attributes = root / ".gitattributes"
    if not attributes.is_file():
        return [".gitattributes 缺失，无法校验行尾规则"]
    rules = _rule_lines(attributes.read_text(encoding="utf-8"))

    if not any(rule == "* text=auto" for rule in rules):
        issues.append(".gitattributes 缺少兜底规则 `* text=auto`（文本默认 LF）")
    if not _has_rule(rules, "*.bat", "crlf"):
        issues.append(".gitattributes 缺少 `*.bat text eol=crlf` 规则")
    if not _has_rule(rules, "*.cmd", "crlf"):
        issues.append(".gitattributes 缺少 `*.cmd text eol=crlf` 规则")
    if not _has_rule(rules, "*.ps1", "lf"):
        issues.append(".gitattributes 缺少 `*.ps1 text eol=lf` 规则")
    if not _has_rule(rules, "*.sh", "lf"):
        issues.append(".gitattributes 缺少 `*.sh text eol=lf` 规则")
    if not _has_rule(rules, "*.command", "lf"):
        issues.append(".gitattributes 缺少 `*.command text eol=lf` 规则")

    agents = root / "AGENTS.md"
    if agents.is_file():
        agents_text = agents.read_text(encoding="utf-8")
        if ".gitattributes" not in agents_text:
            issues.append("AGENTS.md 未把 .gitattributes 指明为换行规则准据")
        for stale in STALE_CLAIMS:
            if stale in agents_text:
                issues.append(f"AGENTS.md 残留过期行尾表述：{stale!r}（应以 .gitattributes 为准）")

    for rel in DISCIPLINE_DOCS[1:]:
        doc = root / rel
        if doc.is_file():
            text = doc.read_text(encoding="utf-8")
            for stale in STALE_CLAIMS:
                if stale in text:
                    issues.append(f"{rel} 残留过期行尾表述：{stale!r}")
    return issues


def main() -> int:
    issues = check()
    if issues:
        print("行尾规则一致性检查失败：", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("行尾规则一致性检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
