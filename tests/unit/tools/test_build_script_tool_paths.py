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
_BUILD_SCRIPTS = ("build_windows.ps1", "build_linux.sh", "build_macos.sh")

#: 匹配 `tools/<路径>.py`，正反斜杠都算（三个平台的写法不同）。
_TOOL_REF_RE = re.compile(r"tools[/\\][\w/\\\-]+\.py")


def _existing_scripts() -> list[str]:
    return [name for name in _BUILD_SCRIPTS if (_REPO / name).is_file()]


def _refs_in(script: str) -> set[str]:
    text = (_REPO / script).read_text(encoding="utf-8", errors="replace")
    return {match.replace("\\", "/") for match in _TOOL_REF_RE.findall(text)}


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
    """每个脚本都应扫到至少几个引用——扫到 0 个说明解析失效。"""
    refs = _refs_in(script)
    assert len(refs) >= 3, (
        f"{script} 只扫到 {len(refs)} 个 tools/*.py 引用，解析可能已失效：{sorted(refs)}"
    )


def test_all_three_platforms_are_covered() -> None:
    """三平台脚本都要在检查范围内——只查一个平台会让另外两个长期失修。"""
    covered = set(_existing_scripts())
    missing_platform = set(_BUILD_SCRIPTS) - covered
    if missing_platform:
        pytest.skip(f"仓库内不含这些平台的构建脚本：{sorted(missing_platform)}")
    assert len(covered) == 3
