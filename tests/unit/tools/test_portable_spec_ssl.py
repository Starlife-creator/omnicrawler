"""便携构建的 spec 必须**显式声明** `ssl` / `_ssl`（W4.1 抓到：macOS 便携包启动即崩）。

## 事故（2026-09-16，`release.yml` 无参派发抓到）

三个平台里 **Windows / Linux 构建绿、macOS 红**，日志里是：

```
File "pyi_rth_nltk.py", … File "nltk/pathsec.py", line 500, in <module>
AttributeError: module 'http.client' has no attribute 'HTTPSConnection'
[PYI-18895:ERROR] Failed to execute script 'pyi_rth_nltk'
```

根因不是 nltk，而是 **`ssl` 没打进冻结包**：CPython 的 `http.client.HTTPSConnection`
**只在 `ssl` 能导入时才被定义**，而 nltk 在模块顶层就用它 ⇒ 起不来。

★ 为什么只有 macOS 暴露：**Win/Linux 的构建 job 只构建、不启动产物**，macOS 的构建脚本会启动
（这正是 W4.2「便携产物内复跑」要补的覆盖面）。

本文件把"每个便携 spec 都显式声明 ssl/_ssl"钉住；`SandboxHost` 是辅助 exe、没有该变量，豁免。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPECS = sorted((_REPO_ROOT / "packaging").glob("OmniCrawler*.spec"))

#: 不含应用运行时的辅助 exe（无 hiddenimports，豁免）
_EXEMPT = {"OmniCrawler-SandboxHost.spec"}


def _has_hiddenimports(text: str) -> bool:
    return re.search(r"^hiddenimports\s*(\+?=)", text, re.MULTILINE) is not None


def test_probe_is_not_vacuous() -> None:
    """先确认扫到了 spec，否则断言会空转。"""
    assert len(_SPECS) >= 4, f"只扫到 {[p.name for p in _SPECS]}"


def test_every_portable_spec_declares_ssl() -> None:
    missing: list[str] = []
    for spec in _SPECS:
        if spec.name in _EXEMPT:
            continue
        text = spec.read_text(encoding="utf-8")
        if not _has_hiddenimports(text):
            missing.append(f"{spec.name}（没有 hiddenimports，应加豁免或补声明）")
            continue
        if not re.search(r'hiddenimports\s*=\s*sorted\(set\(hiddenimports \+ \["ssl", "_ssl"\]\)\)', text):
            missing.append(spec.name)
    assert not missing, (
        "这些 spec 没有显式声明 ssl/_ssl ⇒ 冻结包里 `http.client.HTTPSConnection` 可能缺失，"
        f"nltk 运行时钩子会让 app 起不来（W4.1 事故）：{missing}"
    )


def test_every_portable_spec_excludes_nltk() -> None:
    """每个便携 spec 都要**排除 nltk**（W4.1 事故：它的启动运行时钩子让 app 起不来）。

    两种 spec 写法都要覆盖：① 独立的 `excludes = [...]` 块（Linux/macOS/Windows-Standard）；
    ② 内联在 `common` 字典里的 `excludes=[...]`（Windows Full）。
    """
    missing: list[str] = []
    for spec in _SPECS:
        if spec.name in _EXEMPT:
            continue
        text = spec.read_text(encoding="utf-8")
        if '"nltk"' not in text:
            missing.append(spec.name)
    assert not missing, (
        f"这些 spec 没有排除 nltk ⇒ PyInstaller 会给它装启动钩子（pyi_rth_nltk），"
        f"冻结包一启动就崩（W4.1 事故）：{missing}"
    )
