"""Enforce the ruff-exemption budget: 存量违规只降不升（P2-2）。

背景：`pyproject.toml [tool.ruff.lint] ignore` 长期豁免 10 条规则
（E402/E731/N802/N803/N806/N815/N816/B904/B028 等），豁免一旦存在，
新代码也会"免费"违规 —— 豁免面只增不减。本门禁照
`tools/check_architecture.py` 的 cycle-budget 范式，把被豁免规则的
**存量违规数**记进 `tools/lint-exemption-budget.json`，语义为"不得高于"：

- 新增违规 → 失败（新代码必须自己干净，不许搭存量便车）
- 存量减少 → 通过，并提示同步下调预算（保持零余量）
- 某规则降为 0 → 提示可从 `ignore` 列表回收该规则

本地运行：
    python tools/check_lint_budget.py [--budget tools/lint-exemption-budget.json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET = REPO_ROOT / "tools" / "lint-exemption-budget.json"


def _report_paths(paths: list[str]) -> str:
    return " ".join(paths)


def collect_violations(paths: list[str], rules: list[str]) -> Counter[str]:
    """用 ruff 统计指定规则的违规数（按 rule code 计数）。

    ruff 存在违例时退出码非 0，故 check=False 并解析 JSON 输出。
    """
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        *paths,
        "--select",
        ",".join(sorted(rules)),
        "--output-format",
        "json",
        "--no-cache",
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    payload = completed.stdout.strip()
    if not payload:
        raise SystemExit(
            f"ruff 未返回结果（exit={completed.returncode}）:\n{completed.stderr.strip()[:2000]}"
        )
    try:
        diagnostics = json.loads(payload)
    except json.JSONDecodeError as exc:  # pragma: no cover - 防御性
        raise SystemExit(f"无法解析 ruff JSON 输出: {exc}") from exc
    codes = Counter(item.get("code") or "UNKNOWN" for item in diagnostics)
    return codes


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the ruff exemption budget (ratchet).")
    parser.add_argument("--budget", type=Path, default=DEFAULT_BUDGET)
    args = parser.parse_args()

    payload = json.loads(args.budget.read_text(encoding="utf-8"))
    budget = {code: int(count) for code, count in payload.items() if not code.startswith("_")}
    scope = list((payload.get("_meta") or {}).get("scope", "src tests").split())

    actual = collect_violations(scope, list(budget) or ["E402"])
    failures: list[str] = []
    tighten: list[str] = []

    print(f"ruff exemption budget   ({_report_paths(scope)})")
    print("-" * 62)
    print(f"{'rule':10} {'actual':>8} {'budget':>8}   status")
    for code in sorted(set(budget) | set(actual)):
        limit = budget.get(code, 0)
        count = actual.get(code, 0)
        if count > limit:
            status = "FAIL (新增违规)"
            failures.append(f"{code}: {count} > {limit}")
        elif count < limit:
            status = "ok（可下调预算）"
            tighten.append(f"{code}: {count} < {limit}")
        else:
            status = "ok"
        print(f"{code:10} {count:>8} {limit:>8}   {status}")

    total_actual = sum(actual.values())
    total_budget = sum(budget.values())
    print("-" * 62)
    print(f"{'TOTAL':10} {total_actual:>8} {total_budget:>8}")

    if failures:
        print("\nruff exemption budget exceeded:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        print(
            "\n请清理新增违规，或（如确属不得已）在 PR 说明理由后上调 "
            f"{args.budget.relative_to(REPO_ROOT)}。",
            file=sys.stderr,
        )
        return 1

    if tighten:
        print("\nNOTE 存量已下降，可同步下调预算以保持零余量：")
        for item in tighten:
            print(f"  - {item}")
    zeroed = [code for code in budget if actual.get(code, 0) == 0]
    if zeroed:
        print(
            f"\nNOTE 这些规则已无违规，可从 pyproject.toml ignore 列表回收：{' '.join(sorted(zeroed))}"
        )
    print("ruff exemption budget OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
