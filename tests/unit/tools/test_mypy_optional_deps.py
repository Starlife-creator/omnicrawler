"""mypy 严格档下的**可选依赖差异**：把「只在 CI 上炸」变成本地可验（2026-09-15）。

## 背景（真实事故，`581e74c` 那轮 CI 抓到）

W6.3 把 `core` 纳入 mypy 严格档后，本机（**full 版**，18/18 可选依赖齐全）跑
`mypy src/omnicrawler` 是 **407 文件零问题**，而 CI 的 `test` job 报 2 处 `no-any-return`：

```
src/omnicrawler/core/encoding.py:47: error: Returning Any from function declared to return "str"
src/omnicrawler/core/credentials.py:46: error: Returning Any from function declared to return "str"
```

根因不是代码差异，而是**依赖装得不一样**：本机装了 `chardet` / `keyring` ⇒ mypy 拿到它们的
**类型信息**（返回 `str | None`）；CI 的 test job 没装（extras 里没有它们）⇒
`ignore_missing_imports = true` 生效 ⇒ 调用结果退化成 `Any` ⇒ 严格档立刻报 `no-any-return`。

**修法只能显式收窄**（`return str(...)`）：不能加 `# type: ignore[no-any-return]`，
因为那在**装了依赖的环境**里会触发 `warn_unused_ignores` ⇒ 本机反倒判红。

## 本文件做什么

用 `MYPYPATH` 放**返回 `Any` 的桩**，模拟"依赖缺失"的 mypy 环境，再对**会碰到可选依赖的核心模块**
跑一次严格档检查。于是：

* 写裸 `return some_optional_dep_call(...)` ⇒ **本地就判红**（不必等 CI）；
* 用 `str(...)` / `bytes(...)` 显式收窄 ⇒ 绿。

桩是**故意留空**的（只声明形状、返回 `Any`）——它要模拟的就是"mypy 看不到类型"这件事本身。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mypy") is None, reason="需要 mypy（dev extra）"
)

#: 会调用**可选依赖**、因而在"装了/没装"两种环境下类型不同的核心模块
_OPTIONAL_DEP_MODULES = (
    "src/omnicrawler/core/encoding.py",  # chardet（document extra）
    "src/omnicrawler/core/credentials.py",  # keyring（security extra）
    "src/omnicrawler/core/secrets_store.py",  # cryptography（security extra）
)

#: 桩：只声明"这些调用返回 Any"，模拟 CI 上依赖缺失时 mypy 看到的样子
_STUBS: dict[str, str] = {
    "chardet.pyi": "from typing import Any\ndef detect(data: bytes) -> Any: ...\n",
    "keyring.pyi": "from typing import Any\ndef get_password(service: str, name: str) -> Any: ...\n",
    "cryptography.pyi": textwrap.dedent(
        """
        from typing import Any

        def __getattr__(name: str) -> Any: ...
        """
    ).lstrip(),
}


def test_strict_mypy_survives_missing_optional_dependencies(tmp_path: Path) -> None:
    """缺可选依赖（模拟 CI）时，这些核心模块的严格档检查必须仍然干净。"""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    for name, content in _STUBS.items():
        (stub_dir / name).write_text(content, encoding="utf-8")

    env = dict(os.environ, MYPYPATH=str(stub_dir))
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", *_OPTIONAL_DEP_MODULES],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=300,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    failures = [line for line in output.splitlines() if ": error:" in line]
    assert not failures, (
        "缺可选依赖时严格档报错——这类问题**只在没有该依赖的环境（CI 的 test job）**出现，"
        "修法是用 `str(...)` / `bytes(...)` 显式收窄，而不是加 `type: ignore`"
        "（后者会在装了依赖的环境触发 warn_unused_ignores）：\n  "
        + "\n  ".join(failures[:8])
    )


def test_the_stub_environment_actually_reproduces_the_ci_condition(tmp_path: Path) -> None:
    """守卫要有区分力：桩环境下，**裸返回**可选依赖调用的写法必须被判红。

    做法：临时把一个模块改成裸返回（不改仓库文件，用临时副本），确认它报 `no-any-return`。
    """
    source = _REPO_ROOT / "src" / "omnicrawler" / "core" / "encoding.py"
    text = source.read_text(encoding="utf-8")
    assert "    return str(guess)" in text, "被检查的收窄写法变了，请同步本用例"

    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    for name, content in _STUBS.items():
        (stub_dir / name).write_text(content, encoding="utf-8")

    # 造一个**临时"坏"副本**：模拟"忘了收窄"的写法。
    # ★ 必须让 mypy 认出它是 `omnicrawler.core.*`（否则 `[[tool.mypy.overrides]]` 的严格设置
    #   不生效、`warn_return_any` 没开 ⇒ 不会报 no-any-return，本守卫就成了假通过），
    #   因此临时建出同名包路径，并**从真配置派生**一份最小 mypy 配置给它（避免第二真源）。
    package_root = tmp_path / "src" / "omnicrawler" / "core"
    package_root.mkdir(parents=True)
    (tmp_path / "src" / "omnicrawler" / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "encoding_broken.py").write_text(
        text.replace("    return str(guess)", "    return guess  # 故意裸返回"),
        encoding="utf-8",
    )
    config_path = tmp_path / "mypy-sim.toml"
    config_path.write_text(_derived_mypy_config(), encoding="utf-8")

    env = dict(os.environ, MYPYPATH=str(stub_dir))
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(config_path),
         "src/omnicrawler/core/encoding_broken.py"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=300,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    assert "Invalid value" not in output, (
        "派生的 mypy 配置不是合法 TOML ⇒ mypy 忽略了它（严格档没生效）——"
        "这条路必须判红，否则本门禁是假通过：\n" + "\n".join(output.splitlines()[:8])
    )
    assert "no-any-return" in output, (
        "桩环境没能复现 CI 条件（裸返回竟然不报 no-any-return）——本门禁会变成假通过：\n"
        + "\n".join(output.splitlines()[:8])
    )


def _derived_mypy_config() -> str:
    """从仓库 `pyproject.toml` 取出 `[tool.mypy]` 与本仓 core 的严格 override，拼成临时配置。

    派生而不是手抄：手抄就是第二真源，上游改了严格项这里会静默失效。
    """
    import tomllib

    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mypy = dict(data["tool"]["mypy"])
    mypy.pop("overrides", None)
    strict = next(
        (o for o in data["tool"]["mypy"].get("overrides", []) if o.get("module") == "omnicrawler.core.*"),
        None,
    )
    assert strict is not None, "pyproject 里找不到 omnicrawler.core.* 的严格 override"

    def _render(table: dict[str, object]) -> str:
        return "\n".join(f"{key} = {_toml_value(value)}" for key, value in table.items())

    return f"[tool.mypy]\n{_render(mypy)}\n\n[[tool.mypy.overrides]]\n{_render(strict)}\n"


def _toml_value(value: object) -> str:
    """把 Python 值渲染成**合法 TOML**。

    ★ 别用 `repr()`：Python 的 `True`/`False`/`['x']` 都不是合法 TOML（要 `true`/`["x"]`），
    mypy 会打印 `Invalid value` 并**忽略整份配置** —— 那会让本门禁静默变成假通过
    （实测踩过：严格档没生效，于是"裸返回"也不报错）。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return str(value)
