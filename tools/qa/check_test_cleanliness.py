"""测试洁净度：测试不得往仓库里写任何文件。

## 这个守卫解决什么

本会话遇到一组规律性失败，特征是**单独跑能过、整目录跑就失败**：

| 文件 | 条数 | 症状 |
|---|---|---|
| `tests/unit/pipeline/` | 12 | `Record does not belong to the requested run` |
| `tests/unit/services/` | 18 | `工作区修复记录属于其他配置` |
| `tests/unit/fetching/` | 3 | `reset_login()["moved"]` 期望 0 得 3 |
| `tests/unit/template/` | 1 | 配置历史写到了项目根 |
| `tests/unit/sources/` | 3 | `Scrapy spider不存在`（找去项目根） |

根因只有一个：测试配置里写 `workspace: work`（**相对路径**），
解析到**项目根**，于是同一进程内所有测试共用一个工作目录。

我为此花了四轮迭代才逐个找出来。

## 为什么这条守卫能一次抓住全部

它的判据是「跑完测试后，主仓工作区不许多出任何文件」。
上面每一个 bug 都必然违反它 —— 因为它们都在往仓库里写东西。

这是极强的判据且**零误报**：测试本来就不该污染仓库。

## 为什么除了 `git status` 还要扫被忽略的残留

只看 `git status` 会漏掉最关键的一类：**测试往仓库里写了东西，而 `.gitignore`
恰好把那个形态忽略了**。实测（2026-11-24）仓库里已经躺着 65 个
`src/omnicrawler/plugins/omnicrawler-landlock-*/` 与 37 个根目录 `tmp*/` 空目录，
`git status` 一个都没报 —— 因为 `.gitignore` 为它们各写了一条规则。

忽略规则的用意是「不入库」，不是「可以被生产代码不断制造」。所以本守卫另扫两条
**被忽略的产物形态**（`--ignored=matching` 只列命中的那个目录、不递归，开销可忽略）：

| 形态 | 由谁产生 | 为什么算污染 |
|---|---|---|
| `src/omnicrawler/plugins/omnicrawler-landlock-*` | `plugin_unix_enforcement` 编译原生 shim | 落在源码树里，且 `mypy` 扫 `src/` 时会 `PermissionError` 直接崩 |
| 仓库根 `tmp*` | `tempfile.mkdtemp()` 在临时根不可用时回退到 `cwd` | 同属「跑一次就多一个」，且曾被误提交过 `evil.exe` |

## 为什么放在 tests/ 下而不是 gates/

它需要跑测试才能判定，属于测试基础设施；放进 `--set static` 会让门禁
自己跑一遍测试（60 秒变 10 分钟），违背「本地零负担」。

正确用法：在**跑完测试之后**单独执行，或用 `--set tests` 时由 pytest 自身执行。

## 用法

```powershell
# 在跑过 pytest 之后，检查工作区是否被污染
python tools/qa/check_test_cleanliness.py

# 让它自己跑一遍测试再检查（较慢，适合 CI 或提交前）
python tools/qa/check_test_cleanliness.py --run
```
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

# ★ 直接运行 `python tools/qa/xxx.py` 时 `sys.path[0]` 是 `tools/qa/`，
# 不是仓库根 —— `import tools.child_env` 必然失败。
# 本会话已第五次踩这个坑，故固化在这里。
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.child_env import utf8_env  # noqa: E402

#: 允许被测试改动/新增的路径前缀（相对主仓根）。
#:
#: `.pytest_cache` / `.ruff_cache` 是工具缓存，与测试正确性无关。
#: `artifacts/` 与 `coverage.json` 是 CI 的构建产物（`.gitignore` 已忽略），
#: 它们由工作流显式生成，不属于「测试偷偷写文件」。
#: 被忽略但**仍在仓库里**的残留形态：`(前缀, 说明)`。
#:
#: 这些路径不会被 `git status` 报出来（被 `.gitignore` 忽略），但它们是「跑一次就多一个」
#: 的产物，且落在源码树 / 仓库根。判据是「一个都不许有」—— 容忍它们等于容忍下一次累积。
_IGNORED_LEAK_SHAPES: tuple[tuple[str, str], ...] = (
    (
        "src/omnicrawler/plugins/omnicrawler-landlock-",
        "原生 Landlock shim 的编译目录落在源码树里（plugin_unix_enforcement）",
    ),
    (
        "tmp",
        "仓库根的 ad-hoc 临时目录（mkdtemp 在临时根不可用时回退到 cwd）",
    ),
)


def _git_ignored_entries() -> list[str]:
    """列出被 `.gitignore` 忽略的条目（不递归）。

    `--ignored=matching` 只报「被哪条规则直接命中的那个目录」，而不是像
    `traditional` 那样递归列出其下每个被忽略的条目 —— 后者在本仓会输出上千行。
    """
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--ignored=matching"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=utf8_env(),
        check=False,
    )
    if result.returncode != 0:
        return []
    entries: list[str] = []
    for line in result.stdout.splitlines():
        if not line.startswith("!! "):
            continue
        path = line[3:].strip().strip("\"")
        if path:
            entries.append(path.replace(chr(92), "/").rstrip("/"))
    return entries


def ignored_leaks() -> list[str]:
    """返回仓库里被忽略、但属于「跑一次就多一个」的残留路径。

    判据是**形态匹配**而不是已见名字的白名单：新的 `tmpXXX/` 与
    `omnicrawler-landlock-xxxx/` 都要被抓到，不能只认见过的那些。
    """
    found: set[str] = set()
    for path in _git_ignored_entries():
        head, _, _rest = path.partition("/")
        for prefix, _why in _IGNORED_LEAK_SHAPES:
            if prefix.startswith("src/"):
                if path.startswith(prefix):
                    found.add(path)
            elif head.startswith(prefix):
                found.add(path)
    return sorted(found)


_ALLOWED_PREFIXES = (
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "artifacts/",
    "coverage.json",
    "market/",  # 快照同步会改这里（由 sync_snapshot 负责，不是测试）
)


def _git_status() -> list[str]:
    """返回 `git status --porcelain` 的非空行。"""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=utf8_env(),
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def dirty_paths(lines: list[str]) -> list[str]:
    """从 porcelain 输出里抽出路径（去掉状态前缀）。"""
    paths: list[str] = []
    for line in lines:
        # 格式：`XY path` 或 `XY orig -> path`
        body = line[3:] if len(line) > 3 else ""
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        path = body.strip().strip('"')
        if path:
            paths.append(path.replace("\\", "/"))
    return paths


def filter_allowed(paths: list[str]) -> list[str]:
    """去掉工具缓存等允许的路径。"""
    out: list[str] = []
    for path in paths:
        if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
            continue
        out.append(path)
    return out


def run_tests() -> int:
    """跑一遍单测（用于 `--run` 模式）。"""
    python = _ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python) if python.is_file() else sys.executable
    result = subprocess.run(
        [exe, "-m", "pytest", "tests/unit", "-q", "--no-header"],
        cwd=_ROOT,
        check=False,
    )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验测试没有污染仓库工作区")
    parser.add_argument(
        "--run",
        action="store_true",
        help="先跑一遍单测再检查（较慢）",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="基线文件：写入当前 status 作为基线（之后只报增量）",
    )
    parser.add_argument(
        "--diff-baseline",
        default="",
        help="与基线比对，只报基线之外的新增（本地开发时用，避免把自己的改动当污染）",
    )
    args = parser.parse_args(argv)

    if args.baseline:
        current = filter_allowed(dirty_paths(_git_status())) + ignored_leaks()
        Path(args.baseline).write_text(
            "\n".join(sorted(current)) + "\n", encoding="utf-8"
        )
        print(f"已记录基线（{len(current)} 项）到 {args.baseline}")
        return 0

    if args.run:
        code = run_tests()
        if code != 0:
            print(f"{BAD_MARK} 测试本身没有全通过，先修测试再看洁净度")
            return 1

    paths = filter_allowed(dirty_paths(_git_status()))
    # 被 .gitignore 忽略的残留**也要判红**：忽略规则管的是「不入库」，
    # 不是「可以被生产代码不断制造」—— 只看 git status 时它们完全不可见。
    leaks = ignored_leaks()

    # 基线比对：本地开发时工作区本来就有未提交改动（我自己的编辑），
    # 那些不是「测试污染」。CI 上工作区是干净的，不需要基线。
    if args.diff_baseline:
        try:
            known = {
                line.strip()
                for line in Path(args.diff_baseline).read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        except OSError as exc:
            print(f"{BAD_MARK} 读不到基线 {args.diff_baseline}：{exc}")
            return 2
        paths = [p for p in paths if p not in known]
        leaks = [p for p in leaks if p not in known]

    if not paths and not leaks:
        print(f"{OK_MARK} 工作区干净：测试没有往仓库里写文件，也没有被忽略的残留")
        return 0

    if leaks:
        print(f"{BAD_MARK} 仓库里有 {len(leaks)} 处被忽略的残留（git status 看不见）：")
        for path in leaks:
            print(f"  - {path}")
        print(
            "\n这些目录通常是「跑一次就多一个」，且被 .gitignore 覆盖，所以常规检查全看不见。\n"
            "  * `omnicrawler-landlock-*` ⇒ 原生 Landlock shim 的编译目录落进了源码树\n"
            "  * 仓库根 `tmp*` ⇒ tempfile.mkdtemp 在临时根不可用时回退到了 cwd\n"
            "清理后仍需确保产生它们的代码不再复发，否则下次照旧累积。"
        )

    if paths:
        print(f"{BAD_MARK} 工作区有 {len(paths)} 处改动/新增：")
        for path in paths:
            print(f"  - {path}")
    print(
        "\n测试不得往仓库里写文件。常见原因：\n"
        "  * 测试配置写 `workspace: work`（相对路径）⇒ 落到项目根，"
        "同进程测试共用一个目录\n"
        "  * 直接往 `Path.cwd()` 写文件，而 cwd 是项目根\n"
        "修法：workspace 用绝对路径（tmp_path），或用 conftest 逐测试重置"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
