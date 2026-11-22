"""跨平台矩阵模拟：在本机用**其它平台的身份**跑测试。

## 这个工具解决什么

2026-11-22：CI 的 macOS 腿连红 5 轮、累计 40+ 条失败，根因全是一个模式：

```python
def _recipe(...):
    return {"architectures": ["x86_64"], ...}   # 默认值写死

def test_x():
    check_compatibility(_recipe())              # 隐式继承默认值
```

`check_compatibility` 用 `current_architecture()` 比对。本机是 `x86_64`，
CI 的 macOS runner 是 `aarch64` ⇒ 本地全绿、CI 全红。

**我当时的结论是「这个维度本机永远覆盖不到」。那是错的。**

`current_platform()` / `current_architecture()` 只是两个纯函数，
返回值取决于 `sys.platform` 与 `platform.machine()`。把它们替换掉，
就能在**同一台机器**上逼真地模拟别的平台。

实测：模拟 `macos/aarch64` 跑 `test_component_compatibility.py`，
**3.5 秒**复现了 CI 上那 9 条失败；而发现它花掉了 5 轮 CI（每轮约 40 分钟）。

## 判据

对每个目标平台组合（默认：linux/x86_64、macos/aarch64、windows/x86_64），
把 `current_platform` / `current_architecture` 打上补丁，
跑指定的测试子集，记录失败项。

**退出码**：任一组合有失败 ⇒ 1。这样它能直接当门禁用。

## 边界（必须说清）

* **只模拟「平台名/架构名」这两个函数**。真正的平台差异（文件系统、
  路径分隔符、可执行位、CRLF、字体）模拟不出来 —— 那些仍需真机/CI。
* 它抓的是「按平台名分支的逻辑」这一类，**不是**通用的跨平台测试。
* 默认只跑 `tests/unit/services/` + `tests/unit/browser/` 这类受影响面；
  跑全量会让耗时接近真机全量。

## 用法

```powershell
# 默认矩阵，自动选受影响目录
python tools/qa/platform_matrix.py

# 指定测试与平台
python tools/qa/platform_matrix.py --tests tests/unit/services --platforms macos/aarch64
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

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 默认模拟的平台组合（平台, 架构）。
_DEFAULT_MATRIX: tuple[tuple[str, str], ...] = (
    ("linux", "x86_64"),
    ("macos", "aarch64"),
    ("windows", "x86_64"),
)

#: 默认测试范围：受「平台名分支」影响最大的目录。
_DEFAULT_TESTS: tuple[str, ...] = (
    "tests/unit/services",
    "tests/unit/browser",
)

#: 在子进程里跑测试的引导脚本。
#:
#: 为什么用子进程：`current_platform` / `current_architecture` 的补丁是
#: **进程级**的，而本工具自身要遍历多个组合。放进子进程可以干净隔离，
#: 也避免污染调用方进程。
_BOOTSTRAP = '''
import sys
sys.path.insert(0, {root!r})

from omnicrawler.services import component_recipe as _cr

_platform = {platform!r}
_architecture = {architecture!r}
_probe_platform = lambda: _platform
_probe_architecture = lambda: _architecture

_cr.current_platform = _probe_platform
_cr.current_architecture = _probe_architecture

# **必须同时打补丁到「from ... import」的模块**。
#
# `from .component_recipe import current_platform` 在导入时就把函数对象
# 绑进了模块命名空间 —— 只改 `component_recipe.current_platform` 无效，
# 那些模块仍调用原函数。早先漏了这一步，导致模拟出来的失败是**假象**
# （测试看到的是宿主真实架构）。
import importlib

for _name in (
    "omnicrawler.services.component_install",
    "omnicrawler.services.component_runner",
):
    try:
        _mod = importlib.import_module(_name)
    except Exception:
        continue
    for _attr, _fn in (
        ("current_platform", _probe_platform),
        ("current_architecture", _probe_architecture),
        ("_current", _probe_platform),
    ):
        if hasattr(_mod, _attr):
            setattr(_mod, _attr, _fn)

import pytest
sys.exit(pytest.main({pytest_args!r}))
'''


def run_matrix(
    root: Path,
    tests: list[str],
    platform: str,
    architecture: str,
    extra_args: list[str],
) -> tuple[int, str]:
    """在子进程里以指定平台身份跑测试，返回 `(退出码, 输出)`。"""
    from tools.child_env import utf8_env

    pytest_args = [*tests, "-q", "--no-header", "-p", "no:cacheprovider", *extra_args]
    script = _BOOTSTRAP.format(
        root=str(root / "src"),
        platform=platform,
        architecture=architecture,
        pytest_args=pytest_args,
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=utf8_env(),
        check=False,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def summarize(output: str) -> list[str]:
    """从 pytest 输出里抽失败的用例名。"""
    return [
        line.strip()
        for line in output.splitlines()
        if line.startswith(("FAILED", "ERROR"))
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="在本机用其它平台身份跑测试（模拟 CI 的跨平台矩阵）",
    )
    parser.add_argument(
        "--tests",
        nargs="*",
        default=list(_DEFAULT_TESTS),
        help=f"测试路径（默认：{' '.join(_DEFAULT_TESTS)}）",
    )
    parser.add_argument(
        "--platforms",
        nargs="*",
        default=[],
        help="额外/替代平台组合，格式 `平台/架构`（如 macos/aarch64）",
    )
    parser.add_argument(
        "--extra",
        nargs="*",
        default=[],
        help="透传给 pytest 的额外参数",
    )
    args = parser.parse_args(argv)

    matrix: list[tuple[str, str]] = []
    if args.platforms:
        for item in args.platforms:
            platform, _, architecture = item.partition("/")
            if not platform or not architecture:
                print(f"{BAD_MARK} 平台格式应为 `平台/架构`，收到 {item!r}")
                return 2
            matrix.append((platform, architecture))
    else:
        matrix = list(_DEFAULT_MATRIX)

    root = _ROOT
    failures_total = 0
    summary_lines: list[str] = []

    for platform, architecture in matrix:
        print(f"── {platform}/{architecture} " + "─" * 40)
        code, output = run_matrix(root, args.tests, platform, architecture, args.extra)
        failed = summarize(output)
        if code == 0 and not failed:
            print(f"  {OK_MARK} 通过")
            continue
        failures_total += len(failed)
        print(f"  {BAD_MARK} {len(failed)} 条失败")
        for line in failed[:12]:
            print(f"    {line[:110]}")
        summary_lines.append(f"{platform}/{architecture}: {len(failed)} 条")

    print()
    if failures_total:
        print(f"{BAD_MARK} 共 {failures_total} 条失败（{'；'.join(summary_lines)}）")
        print(
            "\n这些失败**在本机默认平台下不可见**。若是「平台名分支的逻辑」"
            "写死了默认值，改用 current_platform()/current_architecture()；"
            "若是真实的平台行为差异（路径/可执行位/字体），那部分只能靠 CI。"
        )
        return 1

    print(f"{OK_MARK} {len(matrix)} 个平台组合全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
