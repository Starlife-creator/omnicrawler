"""条目 12：`pdf` extra 按支柱拆分，不得把跨支柱依赖绑死在 PDF 上。

**为什么要拆**

Standard 版的构建 extras 是
``gui,html,pdf,browser,async-http,security``（三个平台脚本完全一致），
而 ``pdf`` extra 定义在 `pyproject.toml:26`：

```toml
pdf = ["pypdfium2", "pdfplumber", "pypdf", "reportlab", "openpyxl"]
```

其中两个依赖**不属于文档支柱**：

| 依赖 | 跨支柱的用途 | 后果（若随 pdf 一起拿掉） |
|---|---|---|
| `openpyxl` | `pipeline/exporters.py:279` 的 xlsx 导出 | **爬虫交付能力废掉**（用户采完数据导不出 Excel） |
| `reportlab` | `services/offline_demo.py:27` 的新手引导演示 PDF | **新手引导断掉** |

所以"把 pdf 从 Standard 里去掉"这个动作，**必须先把这两个依赖挪到各自的
extra**，否则会连带砍掉爬虫支柱的功能。

**判据口径**

本守卫不检查"extra 名字是否好看"，而是检查**依赖归属与真实用途一致**：
1. 每个跨支柱库都必须有一个**独立的、名字体现用途的** extra 承载；
2. `pdf` extra **不得**再包含跨支柱库（否则拆了等于没拆）；
3. 代码里的导入必须仍然是**延迟导入**——这样缺依赖时是"该功能不可用"，
   而不是"整个模块导入失败"。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

#: 跨支柱依赖 → 它服务的支柱（用于断言 extra 命名体现用途）
_CROSS_PILLAR = {
    "openpyxl": "excel",
    "reportlab": "pdf-write",
}

#: 文档支柱真正的专属依赖
_DOC_ONLY = ("pypdfium2", "pdfplumber", "pypdf")


def _extras() -> dict[str, list[str]]:
    """粗解析 `[project.optional-dependencies]`：``name = ["...", ...]``。"""
    text = _PYPROJECT.read_text(encoding="utf-8")
    block = text.split("[project.optional-dependencies]", 1)
    assert len(block) == 2, "pyproject 缺少 optional-dependencies 段"
    body = block[1].split("\n[", 1)[0]

    extras: dict[str, list[str]] = {}
    for match in re.finditer(r"^(\w[\w-]*)\s*=\s*\[(.*?)\]", body, re.S | re.M):
        name = match.group(1)
        items = re.findall(r'"([^"]+)"', match.group(2))
        extras[name] = items
    return extras


def _package_names(items: list[str]) -> set[str]:
    """把 ``"openpyxl>=3.1,<4"`` 收敛成 ``openpyxl``（小写）。"""
    names: set[str] = set()
    for item in items:
        name = re.split(r"[<>=!\[; ]", item.strip(), maxsplit=1)[0]
        if name:
            names.add(name.lower())
    return names


def test_cross_pillar_libs_have_their_own_extra() -> None:
    """跨支柱依赖必须有独立 extra —— 否则拿掉 pdf 会连带砍掉爬虫功能。"""
    extras = _extras()
    present = {name: _package_names(items) for name, items in extras.items()}

    problems: list[str] = []
    for package, expected_extra in _CROSS_PILLAR.items():
        # 该包必须出现在某个 extra 里，且那个 extra 不是 pdf
        hosts = [name for name, names in present.items() if package in names]
        if not hosts:
            problems.append(f"{package} 不属于任何 extra（Standard 会永久依赖它）")
            continue
        if "pdf" in hosts:
            problems.append(
                f"{package} 仍被 pdf extra 承载：拿掉 pdf 会连带砍掉非文档能力"
            )
        if expected_extra not in hosts:
            problems.append(
                f"{package} 缺少专用 extra {expected_extra!r}（当前在 {hosts}）"
            )
    assert problems == [], "\n".join(problems)


def test_pdf_extra_keeps_only_document_specific_deps() -> None:
    """`pdf` extra 只能含文档支柱专属依赖。"""
    extras = _extras()
    assert "pdf" in extras, "pdf extra 不应被删除（文档支柱仍需要它）"
    names = _package_names(extras["pdf"])

    leaked = [pkg for pkg in _CROSS_PILLAR if pkg in names]
    assert leaked == [], f"pdf extra 仍包含跨支柱依赖：{leaked}"

    missing = [pkg for pkg in _DOC_ONLY if pkg not in names]
    assert missing == [], f"pdf extra 缺少文档专属依赖：{missing}"


def test_third_party_imports_stay_lazy() -> None:
    """跨支柱库必须**延迟导入**：缺依赖时只该让该功能不可用，不该让模块爆炸。

    判据：`openpyxl` / `reportlab` 不得出现在模块顶层（函数/类外的）import 中。
    """
    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / "src" / "omnicrawler").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:  # 只看模块顶层
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in _CROSS_PILLAR:
                    offenders.append(f"{path.relative_to(_REPO_ROOT).as_posix()}:{node.lineno}")
    assert offenders == [], (
        f"以下位置在模块顶层导入了跨支柱库：{offenders}。"
        "必须改为函数内延迟导入，否则缺该依赖时整个模块无法导入。"
    )
