"""W6-D：声明式视图副作用的**文档承诺**与代码必须双向一致。

## 为什么这条值得做成守卫

`docs/PLUGIN_CONTRACT.md` 里写着「一个声明式视图能触发的**全部**副作用只有四条」。
这句话对插件作者是**行为承诺**——他们据此判断「用声明式界面，我最多能做这些」。

一旦有人在渲染代码里加第五条通道（比如「顺手读个文件」「顺手发个请求」），
文档那句话就变成**假的**，而没有任何东西会红。这类漂移的代价特别高：
它是**安全承诺**的漂移，不是文案漂移。

判据实现只有一份（`tools/gates/check_declarative_side_effects.py`）；
本文件验它**真的会红**，以及渲染模块本身不碰网络/文件。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools.gates import check_declarative_side_effects as gate  # noqa: E402

_HOST = _REPO / "src" / "omnicrawler" / "gui" / "declarative_view_host.py"


def test_gate_passes_on_the_real_repository() -> None:
    """当前仓库必须通过（文档与代码此刻一致）。"""
    assert gate.audit() == []


def test_code_channels_are_exactly_the_documented_four() -> None:
    """★ 代码里的通道集合就是文档写的那四条。"""
    code = gate.adapter_calls(_HOST.read_text(encoding="utf-8"))
    written = gate.documented_channels((_REPO / "docs" / "PLUGIN_CONTRACT.md").read_text(encoding="utf-8"))
    assert code == written, f"代码 {sorted(code)} vs 文档 {sorted(written)}"
    assert len(code) == 4


def test_undocumented_channel_is_detected(tmp_path: Path) -> None:
    """★ 正向：伪造「代码多一条」⇒ 门禁必须报。"""
    host = tmp_path / "src" / "omnicrawler" / "gui" / "declarative_view_host.py"
    host.parent.mkdir(parents=True)
    host.write_text(
        "def f(self):\n"
        "    self._adapter.action()\n"
        "    self._adapter.steal_files()\n",
        encoding="utf-8",
    )
    doc = tmp_path / "docs" / "PLUGIN_CONTRACT.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        gate._DOC_ANCHOR + "\n\n| `adapter.action(...)` | x |\n",
        encoding="utf-8",
    )
    problems = gate.audit(tmp_path)
    assert problems, "多出来的通道没被发现"
    assert "steal_files" in problems[0]


def test_phantom_documented_channel_is_detected(tmp_path: Path) -> None:
    """★ 反向：文档写了代码里没有的通道 ⇒ 门禁必须报（承诺是空的）。"""
    host = tmp_path / "src" / "omnicrawler" / "gui" / "declarative_view_host.py"
    host.parent.mkdir(parents=True)
    host.write_text("def f(self):\n    self._adapter.action()\n", encoding="utf-8")
    doc = tmp_path / "docs" / "PLUGIN_CONTRACT.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        gate._DOC_ANCHOR + "\n\n"
        "| `adapter.action(...)` | x |\n"
        "| `adapter.read_anything(...)` | y |\n",
        encoding="utf-8",
    )
    problems = gate.audit(tmp_path)
    assert problems, "文档里的空承诺没被发现"
    assert any("read_anything" in p for p in problems)


def test_missing_doc_anchor_is_red_not_silent(tmp_path: Path) -> None:
    """★ 改掉承诺措辞就把门禁绕过去是不可能的 —— 找不到锚句必须报。"""
    host = tmp_path / "src" / "omnicrawler" / "gui" / "declarative_view_host.py"
    host.parent.mkdir(parents=True)
    host.write_text("def f(self):\n    self._adapter.action()\n", encoding="utf-8")
    doc = tmp_path / "docs" / "PLUGIN_CONTRACT.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("完全不同的措辞，没有锚句\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="找不到锚句"):
        gate.documented_channels(doc.read_text(encoding="utf-8"))


def test_empty_scan_is_red(tmp_path: Path) -> None:
    """★ 空集合会让所有断言恒真 —— 显式挡住。"""
    host = tmp_path / "src" / "omnicrawler" / "gui" / "declarative_view_host.py"
    host.parent.mkdir(parents=True)
    host.write_text("def f(self):\n    pass\n", encoding="utf-8")
    doc = tmp_path / "docs" / "PLUGIN_CONTRACT.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(gate._DOC_ANCHOR + "\n", encoding="utf-8")
    problems = gate.audit(tmp_path)
    assert any("扫描面为空" in p for p in problems), problems


def test_host_module_does_not_call_network_or_file_apis() -> None:
    """★ 补强：文档说「视图渲染代码本身不碰网络、不碰文件」。这条断言它。

    用 AST 取所有**被调用的函数名**，而不是正则扫源码 —— 后者会把注释与
    文档字符串里提到的 `Path(` 也算进来，导致假红。
    """
    tree = ast.parse(_HOST.read_text(encoding="utf-8"))
    called: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called.append(ast.unparse(node.func))
    joined = "\n".join(called)
    forbidden = [
        ("urllib.request", "网络"),
        ("requests.", "网络"),
        ("socket.", "网络"),
        ("shutil.", "文件"),
        ("Path(", "文件"),
        ("open(", "文件"),
    ]
    hits = [
        f"{token}（{kind}）"
        for token, kind in forbidden
        if re.search(re.escape(token), joined)
    ]
    assert not hits, f"渲染模块直接碰了网络/文件：{hits}"
