"""`rebase_venv.reinstall_editable` 不得联网。

为什么要有这个文件：`run_windows.bat` 每次启动都会跑 `rebase_venv.py`，
而其中的 `reconcile_installed_version` 在「已装元数据版本 ≠ 源码版本」时
会自动 `pip install -e .`。

实测（2026-11-24）：

```
pip install -e .                        -> 90s 超时，卡在 "Installing build dependencies: started"
pip install -e . --no-build-isolation    -> 5.7s 成功
```

pip 默认创建隔离构建环境，从索引**下载** setuptools/wheel。没有外网时就会挂住。
于是「版本不一致」一旦成立，用户**每次双击都要等一次超时**。

而原 docstring 写着「fast and offline-friendly」—— 那句话是假的。

本文件钉住：调用参数里必须带 `--no-build-isolation` 且带 `--no-deps`。
"""

from __future__ import annotations

import ast
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[3] / "tools" / "portable" / "rebase_venv.py"


def _reinstall_fn() -> ast.FunctionDef:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "reinstall_editable":
            return node
    raise AssertionError("reinstall_editable 不见了")


def _pip_invocations(fn: ast.FunctionDef) -> list[list[str]]:
    """收集函数里所有「传给 subprocess 的 pip 参数列表」的字符串字面量。"""
    found: list[list[str]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.List):
            continue
        literals = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if any("pip" in s for s in literals):
            found.append(literals)
    return found


def test_offline_flags_are_present() -> None:
    """参数里必须同时有 `--no-build-isolation` 与 `--no-deps`。"""
    source = _SOURCE.read_text(encoding="utf-8")
    fn_source = ast.get_source_segment(source, _reinstall_fn()) or ""
    assert "--no-build-isolation" in fn_source, "缺 --no-build-isolation ⇒ 会联网下载构建依赖"
    assert "--no-deps" in fn_source, "缺 --no-deps ⇒ 可能重装依赖"


def test_function_still_has_a_fallback() -> None:
    """venv 内缺 setuptools/wheel 时要能退回带隔离的安装。"""
    fn = _reinstall_fn()
    assert any(isinstance(n, ast.For) for n in ast.walk(fn)), (
        "应有 for 循环依次尝试「无隔离」与「有隔离」两种参数"
    )


def test_docstring_no_longer_claims_offline_without_qualification() -> None:
    """原 docstring 说 offline-friendly 但实现会联网 —— 现在要写明前提。"""
    fn = _reinstall_fn()
    doc = ast.get_docstring(fn) or ""
    assert "no-build-isolation" in doc, "docstring 应解释为什么必须带这个参数"


def test_script_is_still_runnable_as_main() -> None:
    """别把 `python rebase_venv.py` 的入口改坏（bat 就是那样调用的）。"""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    has_main_guard = any(
        isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)
        for n in tree.body
    )
    assert has_main_guard
