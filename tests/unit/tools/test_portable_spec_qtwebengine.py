"""条目 1：打包 spec 不得收集代码明确不用的 Qt 运行时。

**依据（这是本守卫的立论）**：
`src/omnicrawler/fetching/login_session.py` 的模块注释明确否决了内嵌 WebEngine——

    不用 QtWebEngine 内嵌（百 MB 级依赖 + 与爬取引擎不同核 ⇒ 指纹不一致），
    而是以外部浏览器…

即：QtWebEngine 是**设计上拒绝**的依赖。全仓 `src/` 检索 `WebEngine` 只有这一处注释，
**零 import**。而 PyInstaller 自带 `hook-PySide6.QtWebEngineCore.py` 等三个 hook，
一旦被任何传递依赖触发，就会连带收集 `Qt6WebEngineCore.dll`（本地实测 194.0 MiB）
与 `resources/` 下的 WebEngine 数据（约 97.9 MiB）。

**为什么必须有机器断言**：spec 的 excludes 漏写不会让任何测试变红，
只会在构建产物体积上悄悄涨——这正是 `test_portable_spec_qml.py` 当年立守卫的同一理由。

**注意口径**：本守卫只断言「spec 文本包含这些排除项」，
不承诺实际节省字节数。真实收益必须由构建后 `tools/check_artifact_budget.py`
按归档体积测量（`.venv` 口径 ≠ 冻结产物口径）。
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPECS = [
    "packaging/OmniCrawler-Standard.spec",
    "packaging/OmniCrawler.spec",
    "packaging/OmniCrawler-Linux.spec",
    "packaging/OmniCrawler-Linux-Full.spec",
    "packaging/OmniCrawler-macOS.spec",
    "packaging/OmniCrawler-macOS-Full.spec",
]

#: 代码零 import、但 PyInstaller hook 会喂进来的 Qt 运行时。
_UNUSED_QT_MODULES = (
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
)


def test_specs_exclude_qtwebengine() -> None:
    """六份 spec 都必须排除 WebEngine —— 产品设计明确不用它。"""
    missing: list[str] = []
    for relative in _SPECS:
        path = _REPO_ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for module in _UNUSED_QT_MODULES:
            if module not in text:
                missing.append(f"{relative}::{module}")
    assert missing == [], (
        "以下 spec 未排除 QtWebEngine：\n  " + "\n  ".join(missing) + "\n"
        "login_session.py 已明确否决内嵌 WebEngine（改用外部浏览器），"
        "排除它是纯收益；若确需内嵌，须先撤销该设计决定并同步本守卫。"
    )


def test_product_source_never_imports_qtwebengine() -> None:
    """产品源码不得 import WebEngine——注释提到可以，import 不行。"""
    import ast

    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / "src" / "omnicrawler").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法错误由别的门禁负责
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any("QtWebEngine" in name for name in names):
                offenders.append(path.relative_to(_REPO_ROOT).as_posix())
                break
    assert offenders == [], (
        f"产品源码出现了 QtWebEngine import：{offenders}。"
        "这与 login_session.py 的既定设计冲突（指纹不一致 + 百 MB 级依赖）。"
    )
