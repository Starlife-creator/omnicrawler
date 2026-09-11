"""Enforce GUI conventions so new pages inherit the design system instead of reinventing it.

背景（2026-09-11 实测）：设计体系的基础资产都已建成——令牌与三套主题、`stylesheet()`、
`assert_no_raw_hex` 守卫、动效信号、图标注册表、无障碍模块、`EmptyState`/`Toast`/
`StatusIndicator`/`LogConsole` 等共享组件。但**继承路径没建立**：

    动效模块接入 0 个视图 / 图标注册表 0 个视图 / EmptyState 仅 3 个视图
    / setAccessibleName 仅 6 个视图 / 9 个文件仍写内联 setStyleSheet

即"新页面各造轮子"。本门禁把三条最硬的约定变成**可执行**约束，使新页面零成本继承：

    A. GUI 控件类必须提供无障碍名（`setAccessibleName` 或 `setAccessibleDescription`）
    B. 不得使用内联 `setStyleSheet`（应走 `design_system.stylesheet()` / 令牌）
    C. 不得出现裸十六进制色值（应以 `design_system.py` 的令牌为准）

存量处理：`tools/gui-conventions-baseline.json` 记录既有违规（**只降不升**）——
不在清单中的文件**零容忍**（新代码立即受约束）；清单内文件不得新增；减少时提示同步下调。
与 `check_lint_budget.py` / `architecture-cycle-budget.json` 同一 ratchet 范式。

用法：
    python tools/check_gui_conventions.py [--baseline tools/gui-conventions-baseline.json]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GUI_ROOT = REPO_ROOT / "src" / "omnicrawler" / "gui"
DEFAULT_BASELINE = REPO_ROOT / "tools" / "gui-conventions-baseline.json"
DESIGN_SYSTEM = GUI_ROOT / "design_system.py"

#: 视为「页面/控件」的 Qt 基类（仅按基类判定，避免用类名后缀猜导致误报）。
WIDGET_BASES = frozenset(
    {
        "QWidget",
        "QDialog",
        "QMainWindow",
        "QFrame",
        "QStackedWidget",
        "QScrollArea",
        "QTabWidget",
        "QGroupBox",
        "QSplitter",
    }
)

HEX_PATTERN = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
A11Y_MARKERS = ("setAccessibleName(", "setAccessibleDescription(")


def _base_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in node.bases:
        text = ast.unparse(base)
        names.add(text.rsplit(".", 1)[-1])
    return names


def _token_hex_whitelist() -> set[str]:
    """令牌真源：`design_system.py` 中出现的全部十六进制色值均视为合法令牌值。

    该模块以动态方式构建运行期白名单（从 VisualTokens 填充），静态解析其字面量
    即可得到同一集合，且免去导入 PySide6 的依赖。
    """
    text = DESIGN_SYSTEM.read_text(encoding="utf-8")
    values = {match.group().upper() for match in HEX_PATTERN.finditer(text)}
    values.update({"#FFFFFF", "#FFF"})  # design_system 明确放行的纯白
    return values


def scan_file(path: Path, whitelist: set[str]) -> dict[str, int]:
    """统计单个文件的三类违规数。"""
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return {"a11y": 0, "inline": 0, "raw_hex": 0}

    missing_a11y = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not (_base_names(node) & WIDGET_BASES):
            continue
        body = ast.get_source_segment(text, node) or ""
        if not any(marker in body for marker in A11Y_MARKERS):
            missing_a11y += 1

    # 规则 B（2026-09-11 收紧）：只拦**纯字面量**样式串——
    # 用令牌拼出来的 f-string（如 f"color: {t.text}"）是设计体系的正确用法，应放行；
    # 写死的颜色无论如何都会被规则 C（裸十六进制）独立拦住。
    inline = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "setStyleSheet"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        value = node.args[0].value
        # 仅统计「非空」字面量样式串：`setStyleSheet("")` 是清除样式（状态复位），
        # 不是写死样式，不应计入（2026-09-11 修正，见 professional_review 的徽章复位）。
        if isinstance(value, str) and value.strip():
            inline += 1
    raw_hex = sum(1 for m in HEX_PATTERN.finditer(text) if m.group().upper() not in whitelist)
    return {"a11y": missing_a11y, "inline": inline, "raw_hex": raw_hex}


def collect(whitelist: set[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for path in sorted(GUI_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path == DESIGN_SYSTEM:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        counts = scan_file(path, whitelist)
        if any(counts.values()):
            result[rel] = counts
    return result


LABELS = {"a11y": "缺无障碍名", "inline": "内联样式", "raw_hex": "裸十六进制"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce GUI conventions (design-system inheritance).")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--write-baseline", action="store_true", help="把当前实测写为 baseline（仅供维护者校准时用）")
    args = parser.parse_args()

    whitelist = _token_hex_whitelist()
    actual = collect(whitelist)

    if args.write_baseline:
        payload = {
            "_meta": {
                "purpose": "GUI 约定的存量违规预算：只降不升。不在本清单中的文件零容忍。",
                "rules": "a11y=控件类缺无障碍名；inline=内联 setStyleSheet；raw_hex=裸十六进制色值",
                "measured": "见文件内容生成时间",
            },
            "files": actual,
        }
        args.baseline.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 baseline：{args.baseline}（{len(actual)} 个文件）")
        return 0

    payload = json.loads(args.baseline.read_text(encoding="utf-8"))
    baseline: dict[str, dict[str, int]] = payload.get("files", {})

    failures: list[str] = []
    tighten: list[str] = []
    new_offenders: list[str] = []

    for rel, counts in sorted(actual.items()):
        limit = baseline.get(rel)
        if limit is None:
            new_offenders.append(f"{rel}: {counts}")
            for key, count in counts.items():
                if count:
                    failures.append(f"{rel}: {LABELS[key]} {count}（新文件，零容忍）")
            continue
        for key, count in counts.items():
            allowed = int(limit.get(key, 0))
            if count > allowed:
                failures.append(f"{rel}: {LABELS[key]} {count} > {allowed}")

    for rel, limit in sorted(baseline.items()):
        counts = actual.get(rel, {"a11y": 0, "inline": 0, "raw_hex": 0})
        for key, allowed in limit.items():
            if counts.get(key, 0) < int(allowed):
                tighten.append(f"{rel}: {LABELS[key]} {counts.get(key, 0)} < {allowed}")

    total_now = sum(sum(c.values()) for c in actual.values())
    total_base = sum(sum(v.values()) for v in baseline.values())
    print(f"GUI 约定检查：{len(actual)} 个文件有存量项 / baseline {len(baseline)} 个文件")
    print(f"违规项合计：当前 {total_now} / baseline {total_base}")
    if new_offenders:
        print(f"\n不在 baseline 的文件出现违规（新代码零容忍，共 {len(new_offenders)} 个）：")
        for item in new_offenders:
            print(f"  - {item}")

    if failures:
        print("\nGUI conventions violated:", file=sys.stderr)
        for item in failures:
            print(f"- {item}", file=sys.stderr)
        print(
            "\n新页面请继承设计体系：控件设 setAccessibleName；样式走 design_system.stylesheet()；"
            "颜色用 VisualTokens 令牌。确需例外时在 PR 说明理由并更新 baseline。",
            file=sys.stderr,
        )
        return 1

    if tighten:
        print("\nNOTE 存量已下降，可同步下调 baseline 以保持零余量：")
        for item in tighten[:12]:
            print(f"  - {item}")
        if len(tighten) > 12:
            print(f"  ...（其余 {len(tighten) - 12} 项）")

    remaining = len(actual)
    print(f"GUI conventions OK（仍待收敛的存量文件 {remaining} 个）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
