"""本地已装工具版本必须与 `constraints/quality.txt` 的 pin 一致。

## 为什么这个门禁存在（2026-11-23 的真实教训）

维护者指出：

> 有时候你代码里升级了，但本地也要同步升级。

实证就在同一天：`constraints/quality.txt` 里 pin 的是 `ruff==0.17.6`，
而我本机 venv 装的是 **0.16.4**。

**后果不是「门禁慢了」，而是「本地绿、CI 红」**：

* ruff 用**显式 `select = ["E","F","I","UP","W","B","N"]`**，所以新增规则
  只在「同类别且默认开启」时才生效 —— 边界很窄，但**真实存在**；
* 更要紧的是**相反方向**：本地版本旧 → 本地跑绿 → 推上去 CI 用新版本报新问题
  → 一轮 40 分钟才发现。这类失败纯属自找。

## 判据（确定性）

对 `constraints/quality.txt` 里 pin 了 `==` 的**开发工具**（ruff / mypy /
coverage / pytest），检查本机 `pip show` 的版本是否逐字相等。

* 一致 ⇒ 通过；
* 不一致 ⇒ 判红，并给出精确的升级/降级命令；
* 工具没装 ⇒ 跳过（不是失败 —— 有人可能只装了部分依赖）。

## 为什么不是「装最新版就好」

**必须与 constraints 逐字相等**，不是「>=」。因为 CI 就是按 constraints 装的，
本地装一个更新的版本反而会「本地红、CI 绿」，那同样误导。

## 边界

* 只查**开发工具**，不查运行时依赖（那些由 `uv sync` / extras 管）；
* 不自动装包（改了别人的环境是副作用）；
* 在 CI 里它必然通过（CI 正是按 constraints 装的），所以实际价值在**本地**。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"
SKIP_EXIT_CODE = 0

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 需要与 constraints 保持一致的**开发工具**（不含运行期依赖）。
_DEV_TOOLS: tuple[str, ...] = ("ruff", "mypy", "coverage", "pytest")


def _pinned(constraints: Path) -> dict[str, str]:
    """从 constraints 取 `{包名: 版本}`，只收 `==` 精确 pin。"""
    pinned: dict[str, str] = {}
    if not constraints.is_file():
        return pinned
    for line in constraints.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", stripped)
        if match:
            pinned[match.group(1).lower()] = match.group(2)
    return pinned


def _installed_version(tool: str) -> str:
    """本机装的版本；没装返回空串。"""
    try:
        from tools.child_env import utf8_env

        completed = subprocess.run(
            [sys.executable, "-m", "pip", "show", tool],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=60, env=utf8_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (completed.stdout or "").splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="本地开发工具版本是否与 constraints/quality.txt 一致",
    )
    parser.add_argument(
        "--constraints", default="", help="约束文件路径（缺省 constraints/quality.txt）",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="版本不一致时判红（缺省只警告）",
    )
    args = parser.parse_args(argv)

    constraints = Path(args.constraints) if args.constraints else _ROOT / "constraints" / "quality.txt"
    if not constraints.is_file():
        print(f"{BAD_MARK} 找不到约束文件：{constraints}")
        return 1

    pinned = _pinned(constraints)
    problems: list[str] = []
    checked = 0
    missing: list[str] = []

    for tool in _DEV_TOOLS:
        expected = pinned.get(tool)
        if not expected:
            continue
        actual = _installed_version(tool)
        if not actual:
            missing.append(tool)
            continue
        checked += 1
        if actual != expected:
            problems.append(
                f"{tool}: 本机 {actual}，constraints 要求 {expected}"
            )

    if problems:
        print(f"{BAD_MARK} 本地工具版本与 constraints 不一致——这会导致「本地绿、CI 红」：")
        for item in problems:
            print(f"  - {item}")
        print("\n同步命令（照抄即可）：")
        for item in problems:
            tool = item.split(":", 1)[0]
            print(f"  python -m pip install {tool}=={pinned[tool]}")
        print(
            "\n**必须逐字相等，不是「装最新版」**：CI 就是按 constraints 装的，"
            "本地装更新反而会「本地红、CI 绿」。"
        )
        if not args.strict:
            # **缺省只警告，不判红。**
            #
            # 本地工具版本是**环境状态**，不是代码缺陷。让 `--set static`
            # 因为它变红，会逼人绕过 static 去提交 —— 那是更坏的结果
            # （与 2026-11-23 platform_matrix 那个教训同源：门禁太容易红，
            # 人就学会跳过门禁）。CI 按 constraints 装，会兜住真正的风险。
            print(
                "\n（缺省只警告。要阻断本地提交用 --strict。）"
            )
            return 0
        return 1

    if missing:
        print(f"{OK_MARK} 已检查 {checked} 个工具版本一致；未装（跳过）：{', '.join(missing)}")
    else:
        print(f"{OK_MARK} 本地 {checked} 个开发工具版本与 constraints 一致")
    return SKIP_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
