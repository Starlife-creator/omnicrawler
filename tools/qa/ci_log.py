"""CI 日志包装：把 `gh` 的原始日志变成**能读的失败摘要**。

## 这个工具解决什么

本会话我为了看 CI 失败，反复敲这一串：

```powershell
$j = gh run view <id> --repo <r> --json jobs --jq '.jobs[0].databaseId'
$log = gh api repos/<r>/actions/jobs/$j/logs | Out-String
$clean = $log -replace '\\x1b\\[[0-9;]*m',''
($clean -split "`n" | Select-String -Pattern "✗|✓|失败" | Select-Object -First 12)
```

三个问题：

1. **要手工过滤 ANSI 转义**（`\\x1b[...m`），不过滤就是一堆乱码；
2. **每行开头都有 ISO 时间戳**，占掉一半宽度；
3. **不知道是哪一步红的** —— 得自己找 `##[error]` 与 `::error::`。

## 这个工具做什么

```powershell
python tools/qa/ci_log.py                    # 最近一次失败的 run
python tools/qa/ci_log.py --run 44905195409  # 指定 run
python tools/qa/ci_log.py --errors-only      # 只看错误行
```

它输出三段：

1. **概览**：run 的结论、各 job 结论
2. **失败步骤**：哪个 job 的哪个 step 红的，附该步骤的末尾输出
3. **关键行**：`##[error]`、`::error::`、`FAILED`、`✗` 等

## 为什么保留「全部日志」选项

`--raw` 输出清洗后的完整日志。摘要只摘**看起来像错误**的行，
但真正的根因有时藏在正常输出里 —— 需要能退回全文看。

## 依赖

`gh` CLI（已登录）。本工具不自己发 HTTP 请求：
`gh` 处理了认证、重定向与 API 版本，重新实现一遍只会更脆弱。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: ANSI 转义序列（颜色、光标移动等）。
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: GitHub Actions 的时间戳前缀，如 `2026-11-21T12:44:06.9330182Z `。
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")

#: 看起来像错误的行（摘要用）。
_ERROR_HINTS = re.compile(
    r"##\[error\]"
    r"|::error::"
    r"|^\s*E\s{2,}"          # pytest 的断言详情行
    r"|^FAILED\b"
    r"|^ERROR\b"
    r"|✗"
    r"|\[FAIL\]"
    r"|Traceback \(most recent call last\)"
    r"|Error:"
    r"|错误[:：]"
)


def _run_gh(args: list[str], timeout: int = 120) -> tuple[int, str]:
    """跑一条 `gh` 命令，返回 `(退出码, 输出)`。"""
    from tools.child_env import utf8_env

    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=utf8_env(),
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{BAD_MARK} 无法执行 gh：{exc}"
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def clean(raw: str) -> str:
    """去掉 ANSI 转义与时间戳前缀。"""
    lines = []
    for line in raw.splitlines():
        line = _ANSI.sub("", line)
        line = _TIMESTAMP.sub("", line)
        lines.append(line)
    return "\n".join(lines)


def find_default_run() -> str:
    """找最近一次失败的 run（找不到就取最近一次）。"""
    for filter_args in (["--status", "failure"], []):
        code, output = _run_gh(
            [
                "run",
                "list",
                *filter_args,
                "--limit",
                "1",
                "--json",
                "databaseId",
                "--jq",
                ".[0].databaseId",
            ]
        )
        if code == 0 and output.strip():
            return output.strip()
    return ""


def run_overview(run_id: str) -> str:
    """取 run 的结论与各 job 状态。"""
    code, output = _run_gh(
        [
            "run",
            "view",
            run_id,
            "--json",
            "conclusion,displayTitle,jobs",
            "--jq",
            (
                '"结论: \\(.conclusion)  标题: \\(.displayTitle)",'
                ' (.jobs[] | "  \\(.conclusion // "?")  \\(.name)")'
            ),
        ]
    )
    if code != 0:
        return f"{BAD_MARK} 取 run 概览失败：{output.strip()[:200]}"
    return output.strip()


def job_logs(run_id: str) -> list[tuple[str, str]]:
    """取每个 job 的日志。返回 `[(job 名, 清洗后日志)]`。"""
    code, output = _run_gh(
        [
            "run",
            "view",
            run_id,
            "--json",
            "jobs",
            "--jq",
            '.jobs[] | "\\(.databaseId)\\t\\(.name)"',
        ]
    )
    if code != 0:
        return []

    result: list[tuple[str, str]] = []
    for line in output.splitlines():
        if "\t" not in line:
            continue
        job_id, _, name = line.partition("\t")
        job_id, name = job_id.strip(), name.strip()
        if not job_id.isdigit():
            continue
        # `gh api` 默认**拒绝输出含 ANSI 转义的响应**：
        #   "the response contains terminal escape sequences;
        #    pass --allow-escape-sequences to output it anyway"
        # 而 GitHub Actions 的原始日志必然带颜色转义 —— 必须显式放行，
        # 由本工具的 `clean()` 负责剥掉。
        log_code, log_text = _run_gh(
            [
                "api",
                "--allow-escape-sequences",
                f"repos/{{owner}}/{{repo}}/actions/jobs/{job_id}/logs",
            ],
            timeout=180,
        )
        if log_code != 0:
            result.append((name, f"{BAD_MARK} 取日志失败：{log_text.strip()[:200]}"))
            continue
        result.append((name, clean(log_text)))
    return result


def _payload(text: str) -> str:
    """从 `##[group]Run` 段里剥出命令本身（便于看这一步在做什么）。"""
    lines = text.splitlines()

    # ★ 取**报错前最后一个** `##[group]Run`，不是第一个。
    #
    # 第一个往往是 runner 自己的信息段（"Hosted Compute Agent / Version …"），
    # 与失败原因无关。失败的那一步通常是**报错行之前最近的那个** Run 组。
    error_index = len(lines)
    for index, line in enumerate(lines):
        if _ERROR_HINTS.search(line):
            error_index = index
            break

    candidate = ""
    for index, line in enumerate(lines[:error_index]):
        if "##[group]Run" not in line:
            continue
        body: list[str] = []
        for follow in lines[index + 1 :]:
            if follow.startswith("##[endgroup]"):
                break
            body.append(follow)
        stripped = "\n".join(body).strip()
        # 跳过 runner 元信息段（它们没有命令行的样子）
        if stripped and "Hosted Compute Agent" not in stripped:
            candidate = stripped
    return candidate


def summarize(name: str, text: str, *, errors_only: bool) -> str:
    """把一个 job 的日志压成失败摘要。"""
    lines = text.splitlines()
    if errors_only:
        picked = [line for line in lines if _ERROR_HINTS.search(line)]
    else:
        picked = [
            line
            for line in lines
            if _ERROR_HINTS.search(line) or line.startswith("##[group]Run")
        ]

    chunks = [f"\n### {name}"]
    if not picked:
        chunks.append("  （没有匹配到错误行 —— 用 --raw 看全文）")
        return "\n".join(chunks)

    seen: set[str] = set()
    for line in picked:
        stripped = line.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        chunks.append(f"  {stripped[:220]}")

    # 附上第一条 `##[group]Run` 的命令体，便于知道失败的那一步在做什么
    command = _payload(text)
    if command:
        chunks.append("  ── 首个步骤的命令 ──")
        for line in command.splitlines()[:4]:
            chunks.append(f"    {line[:200]}")
    return "\n".join(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 gh 的原始 CI 日志变成可读的失败摘要",
    )
    parser.add_argument("--run", default="", help="run id（缺省＝最近一次失败的）")
    parser.add_argument("--errors-only", action="store_true", help="只看错误行")
    parser.add_argument("--raw", action="store_true", help="输出清洗后的完整日志")
    args = parser.parse_args(argv)

    run_id = args.run.strip() or find_default_run()
    if not run_id:
        print(f"{BAD_MARK} 找不到可用的 run（gh 未登录，或仓库没有运行记录）")
        return 2

    print(f"run {run_id}")
    print("─" * 70)
    print(run_overview(run_id))
    print("─" * 70)

    logs = job_logs(run_id)
    if not logs:
        print(f"{BAD_MARK} 取不到 job 日志")
        return 1

    if args.raw:
        for name, text in logs:
            print(f"\n===== {name} =====")
            print(text)
        return 0

    for name, text in logs:
        print(summarize(name, text, errors_only=args.errors_only))

    print("\n" + "─" * 70)
    print("提示：`--raw` 看全文；`--errors-only` 只看错误行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
