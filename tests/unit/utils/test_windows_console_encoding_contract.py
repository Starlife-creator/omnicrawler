"""Windows CI 的控制台编码：**job 级必须设 `PYTHONIOENCODING=utf-8`**。

## 背景（2026-09-15 实测，为此连续烧掉三个 CI 周期）

runner 的 stdout 默认是 **cp1252**，而 `tools/` 下的脚本会打印中文（门禁结论、错误提示）
⇒ 只要有非 ASCII 就 `UnicodeEncodeError: 'charmap' codec` 直接崩：

* `check_coverage_gates.py`（我新加的打印用了全角括号）；
* `check_gui_conventions.py`（它此前被上一个崩溃"挡住"，修好前一个之后才轮到它炸）。

而 **ubuntu / macOS 是 UTF-8，完全没有症状** ⇒ 这类缺陷**只在 Windows job 上暴露**。
清单里 22 个工具脚本都在打印中文 —— 逐个改文案是错的方向（几百处、还伤可读性），
**正确做法是修环境**：在 workflow 的 **job 级** `env:` 设 `PYTHONIOENCODING=utf-8`，
让所有步骤继承（而不是在各个步骤里重复）。

**为什么用 `PYTHONIOENCODING` 而不是 `PYTHONUTF8`**：前者只改 **stdio** 编码；
后者会连带把 `open()` 的默认编码也变成 UTF-8，可能影响读文件的测试（blast radius 更大）。

本文件把这条约定**机器化**：谁把这两行删了，测试立刻红。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"

#: 会跑 Windows、且会执行 `tools/` 脚本的工作流
WINDOWS_CAPABLE = ("quality.yml", "release.yml")


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _workflow_level_env(workflow: str) -> str:
    """取 workflow **顶层** `env:` 段（从 `env:` 行到下一个顶层键为止）。

    顶层 env 会被所有 job/step 继承 —— 这正是我们要的（不必在每个 step 里重复设置）。
    """
    lines = workflow.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("env:")), None)
    assert start is not None, "未找到 workflow 顶层 env: 段"
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith((" ", "\t")):
            break
        block.append(line)
    return "\n".join(block)


def test_windows_capable_workflows_force_utf8_stdio() -> None:
    """每个会跑 Windows 的工作流都必须在顶层 env 里设 `PYTHONIOENCODING=utf-8`。"""
    for name in WINDOWS_CAPABLE:
        env_block = _workflow_level_env(_workflow(name))
        assert re.search(r'PYTHONIOENCODING:\s*"?utf-8"?', env_block), (
            f"{name} 的顶层 env 未设 PYTHONIOENCODING=utf-8：Windows runner 的 stdout 是 cp1252，"
            "tools/ 脚本打印中文会 UnicodeEncodeError 崩溃（2026-09-15 实测）"
        )


def _has_windows_runner(workflow: str) -> bool:
    """是否真的会跑 Windows：要么本文件里有 `windows-latest`，要么调用了 Windows 的可复用构建。

    （`release.yml` 属后者：它自己只在 ubuntu 上跑聚合 job，Windows 构建在
    `reusable-build-windows.yml` 里 —— 写成"必须出现 windows-latest"会误判。）
    """
    return "windows-latest" in workflow or "reusable-build-windows" in workflow


def test_the_guard_is_not_vacuous() -> None:
    """守卫要有意义：这些工作流必须**真的**跑 Windows（否则这条契约在自说自话）。"""
    for name in WINDOWS_CAPABLE:
        assert _has_windows_runner(_workflow(name)), f"{name} 不跑 Windows，本契约不适用"


def test_reason_is_documented_in_the_workflow() -> None:
    """环境变量的**理由**要写在 workflow 里 —— 否则后人只会看到一行"多余的"配置。

    反例（本项目已登记）：依赖「人记得为什么」的配置最终会被当成冗余删掉。
    """
    for name in WINDOWS_CAPABLE:
        assert "cp1252" in _workflow(name), f"{name} 未写明为什么要设 PYTHONIOENCODING"
