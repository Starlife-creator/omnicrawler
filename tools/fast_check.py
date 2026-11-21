"""秒级自检：语法 + 静态检查 + 单文件类型检查。

## 为什么需要它

`self_verify.py --set static` 要 30–60 秒。改一行代码等一分钟，
实际后果是「先写下去、等门禁再说」——于是**改坏结构要等一分钟才发现**。

本会话里我用批量脚本改结构化代码，四次改坏（元组粘连、import 插进
括号中间、`_()` 插入位置错、变量作用域错）。每次都是靠后面的门禁或
测试才发现，中间浪费的是一整个反馈循环。

这个脚本只做**三件毫秒到秒级的事**：

1. `ast.parse` 每个改动的 `.py` 文件 —— 语法错误 0.1 秒暴露；
2. `ruff check` 只看改动的文件 —— 未使用导入、行太长等；
3. `mypy` 只检查改动的文件 —— 类型错误，因为限制到单文件所以很快。

## 用法

```powershell
# 检查工作区里相对 git HEAD 改动的文件（--staged 只看已暂存）
python tools/fast_check.py

# 检查指定文件
python tools/fast_check.py src/omnicrawler/gui/views/download_channels.py

# 全量语法检查（仍比 static 快得多：不跑门禁、不做跨文件分析）
python tools/fast_check.py --all
```

退出码：`0` 全过 · `1` 有问题 · `2` 输入不可读。

## 与 `--set static` 的分工

本脚本**不替代**门禁：它不检查锚完整性、不检查文档数字、不检查跨文件
架构约束。它是「提交前的第一道闸」，用来在**最便宜的时机**暴露
最廉价的错误。真正的发布判据仍然是 `self_verify.py --set static`。
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

from tools.child_env import utf8_env

#: 项目根（`tools/` 的上一级）。
_ROOT = Path(__file__).resolve().parents[1]

#: 只检查这些后缀（mypy / ruff 只对 Python 有意义）。
_PY_SUFFIXES = (".py",)


def _git(*args: str) -> list[str]:
    """跑一条 git 命令，返回非空行。失败返回空列表。"""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=utf8_env(),
            check=False,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def changed_files(*, staged_only: bool = False) -> list[Path]:
    """相对 git HEAD 改动（含未暂存与未跟踪）的 Python 文件。"""
    if staged_only:
        names = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    else:
        names = _git("diff", "HEAD", "--name-only", "--diff-filter=ACMR")
        names += _git("ls-files", "--others", "--exclude-standard")
    out: list[Path] = []
    for name in dict.fromkeys(names):  # 去重且保序
        if not name.endswith(_PY_SUFFIXES):
            continue
        path = (_ROOT / name).resolve()
        if path.is_file():
            out.append(path)
    return out


def all_python_files() -> list[Path]:
    """仓库里全部受版本控制的 Python 文件。"""
    out: list[Path] = []
    for name in _git("ls-files", "*.py"):
        path = (_ROOT / name).resolve()
        if path.is_file():
            out.append(path)
    return out


def check_syntax(paths: list[Path]) -> list[str]:
    """`ast.parse` 每个文件。这是最便宜、也最该最先跑的检查。"""
    problems: list[str] = []
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path}: 无法读取（{exc}）")
            continue
        try:
            ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            where = f"{exc.lineno}:{exc.offset}" if exc.lineno else "?"
            problems.append(f"{path}:{where} 语法错误：{exc.msg}")
    return problems


def _run_tool(argv: list[str], label: str) -> list[str]:
    """跑一个外部工具；把它的输出原样当问题返回。"""
    try:
        result = subprocess.run(
            argv,
            cwd=_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=utf8_env(),
            check=False,
        )
    except OSError as exc:
        return [f"{label}: 无法执行（{exc}）"]
    if result.returncode == 0:
        return []
    text = (result.stderr or "") + (result.stdout or "")
    lines = [line for line in text.splitlines() if line.strip()]
    return lines or [f"{label}: 退出码 {result.returncode}（无输出）"]


def check_ruff(paths: list[Path]) -> list[str]:
    ruff = _ROOT / ".venv" / "Scripts" / "ruff.exe"
    exe = str(ruff) if ruff.is_file() else "ruff"
    return _run_tool([exe, "check", *[str(p) for p in paths]], "ruff")


def check_mypy(paths: list[Path]) -> list[str]:
    python = _ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python) if python.is_file() else sys.executable
    # 单文件 mypy 仍会解析依赖，但比全量快一个数量级。
    return _run_tool(
        [exe, "-m", "mypy", "--no-error-summary", *[str(p) for p in paths]],
        "mypy",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="秒级自检：语法 + ruff + 单文件 mypy",
    )
    parser.add_argument("files", nargs="*", help="要检查的文件（缺省＝改动的文件）")
    parser.add_argument("--all", action="store_true", help="检查全部受版本控制的 .py")
    parser.add_argument("--staged", action="store_true", help="只看已暂存的改动")
    parser.add_argument("--no-mypy", action="store_true", help="跳过 mypy（更快）")
    args = parser.parse_args(argv)

    if args.files:
        paths = [Path(item).resolve() for item in args.files]
        missing = [p for p in paths if not p.is_file()]
        if missing:
            print(f"找不到文件：{missing}", file=sys.stderr)
            return 2
    elif args.all:
        paths = all_python_files()
    else:
        paths = changed_files(staged_only=args.staged)

    if not paths:
        print("没有需要检查的文件（工作区干净）。")
        return 0

    print(f"检查 {len(paths)} 个文件…")

    problems = check_syntax(paths)
    if problems:
        # 语法不过就不必再跑后面的了 —— 它们只会重复报同一个错。
        print("\n".join(problems))
        print(f"\n[FAIL] 语法：{len(problems)} 个问题")
        return 1
    print("  [OK] 语法")

    ruff_problems = check_ruff(paths)
    if ruff_problems:
        print("\n".join(ruff_problems))
        print(f"\n[FAIL] ruff：{len(ruff_problems)} 行输出")
        return 1
    print("  [OK] ruff")

    if not args.no_mypy:
        mypy_problems = check_mypy(paths)
        if mypy_problems:
            print("\n".join(mypy_problems))
            print(f"\n[FAIL] mypy：{len(mypy_problems)} 行输出")
            return 1
        print("  [OK] mypy")

    print("\n[OK] 快速自检通过（发布判据仍需 self_verify.py --set static）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
