"""构建脚本引用的 `tools/*.py` 必须真实存在——按职责域重组后最容易漏改的地方。

## 为什么需要这条守卫

`e32d58ff` 把工具脚本按职责域拆进 `tools/{gates,builders,releasing,portable}/`，
但**只更新了一部分调用方**。后果是真实 CI 上：

* Linux 构建：`can't open file 'tools/check_portable_doc_manifest.py'` → exit 2
* macOS 构建：同类路径缺失 5 处

而本地静态门禁**全绿**——因为构建脚本本身不被「文件是否存在」检查覆盖。

这类缺陷的形态很特别：**脚本语法正确、逻辑正确，只是文件搬家后没人更新路径**。
它只会在真正跑构建时暴露，也就是只在 CI 上暴露。

## 判据

三个构建脚本（`build_windows.ps1` / `build_linux.sh` / `build_macos.sh`）里出现的每个
`tools/<相对路径>.py` 引用，都必须在仓库里真实存在。

## 边界（如实说明）

**验证的是"路径存在"，不是"参数正确"**。文件在、但参数被改坏，本守卫查不出来——
那要靠构建本身跑通。本守卫只保证"不会因为搬家而失败"这一类。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]

#: 所有会调用 `tools/*.py` 的构建与打包脚本。
#:
#: **为什么要包含 `packaging/`**：运行时准备脚本 `prepare_{linux,macos}_runtime.sh`
#: 也调用工具脚本。第一次只扫了根目录三个构建脚本，漏掉它们 ⇒ 真实 CI 上
#: Linux 构建在 `can't open file 'tools/download_and_smoke_test.py'` 处失败。
_BUILD_SCRIPTS = (
    "build_windows.ps1",
    "build_linux.sh",
    "build_macos.sh",
    "packaging/prepare_linux_runtime.sh",
    "packaging/prepare_macos_runtime.sh",
)

#: 匹配 `tools/<路径>.py`，正反斜杠都算（三个平台的写法不同）。
#: `(?<!\w)` 保证不把 `tests/unit/tools/xxx.py` 里的片段当成 `tools/xxx.py`；
#: 但**必须允许**前面是 `/` 或 `$PROJECT_ROOT/` —— 构建脚本的写法是
#: `"$PROJECT_ROOT/tools/gates/x.py"`，若把 `/` 也排除就会漏掉全部真实引用。
_TOOL_REF_RE = re.compile(r"(?<!\w)tools[/\\][\w/\\\-]+\.py")


def _existing_scripts() -> list[str]:
    return [name for name in _BUILD_SCRIPTS if (_REPO / name).is_file()]


def _refs_in(script: str) -> set[str]:
    """脚本里**真正会被执行**的 `tools/*.py` 引用。

    跳过注释行：构建脚本的注释里会提到守卫测试路径
    （如 `# 守卫：tests/unit/tools/test_xxx.py`），那**不是**执行引用，
    把它算进来会让守卫对本该通过的脚本判红 —— 那是守卫自己的判据错误。
    """
    text = (_REPO / script).read_text(encoding="utf-8", errors="replace")
    executable_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "//", "<!--")):
            continue
        executable_lines.append(line)
    body = "\n".join(executable_lines)
    return {match.replace("\\", "/") for match in _TOOL_REF_RE.findall(body)}


def test_build_scripts_are_present() -> None:
    """先确认真的扫到了构建脚本——否则下面的空集对空集会假通过。"""
    found = _existing_scripts()
    assert found, f"没找到任何构建脚本（找过 {_BUILD_SCRIPTS}）"


@pytest.mark.parametrize("script", _existing_scripts())
def test_referenced_tool_scripts_exist(script: str) -> None:
    """脚本里引用的每个 tools/*.py 都必须存在。

    这条正是 Linux/macOS 构建在 v1.0.3 首次真实 CI 上失败的原因：
    `tools/check_portable_doc_manifest.py` 早被搬进 `tools/gates/`，
    但两个 shell 脚本没跟着改。
    """
    refs = _refs_in(script)
    assert refs, f"{script} 里没扫到任何 tools/*.py 引用 —— 正则可能失效"

    missing = sorted(ref for ref in refs if not (_REPO / ref).is_file())
    assert not missing, (
        f"{script} 引用了不存在的脚本：{missing}。"
        "工具脚本按职责域重组后调用方必须同步更新路径。"
    )


@pytest.mark.parametrize("script", _existing_scripts())
def test_scan_is_not_vacuous(script: str) -> None:
    """每个脚本都应扫到至少 1 个引用——扫到 0 个说明解析失效。

    阈值取 1，因为各脚本的调用量天然不同，按实测分别确定：

    | 脚本 | 实际调用 |
    |---|---|
    | `build_*.{sh,ps1}` | 10–12 个 |
    | `packaging/prepare_linux_runtime.sh` | 2 个 |
    | `packaging/prepare_macos_runtime.sh` | **1 个**（只调 `build_common.py`） |

    把阈值统一抬高会让阈值本身变成假红来源——那是守卫的判据错误，
    不是产品缺陷。这里只防「正则失效导致扫到 0 个」这一种情况。
    """
    refs = _refs_in(script)
    assert len(refs) >= 1, (
        f"{script} 一个 tools/*.py 引用都没扫到，解析已失效"
    )


def test_all_platform_entrypoints_are_covered() -> None:
    """三个平台的**构建入口**都要在检查范围内。

    不再断言「恰好 3 个脚本」——`packaging/prepare_*_runtime.sh` 也是入口，
    以后还可能加。判据是「三个平台的构建入口都在」，而不是「数量对得上」。
    """
    covered = set(_existing_scripts())
    for entry in ("build_windows.ps1", "build_linux.sh", "build_macos.sh"):
        if not (_REPO / entry).is_file():
            pytest.skip(f"仓库内不含 {entry}")
        assert entry in covered, f"{entry} 不在检查范围内"
