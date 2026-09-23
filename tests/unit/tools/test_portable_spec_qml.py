"""Q1 试点：打包 spec **不得**收集 QML 页面文件与 QML 运行时（2026-09-23 拍板）。

拍板内容：QML 橱窗页**保留代码**、发布包**不打包**（Qt6Qml/Quick/ShaderTools 与
qml/ 目录合计 ≈45 MB，只为一个只读演示页不值得背进便携包）。冻结包里该页
**显式降级**（`qml_showcase.qml_available` 判 ImportError 或页面文件缺失），
所以"不打包"必须有机器断言钉住——缺了本守卫，将来加回 datas 会无声回归。

与 `test_portable_spec_ssl.py` 同一范式：把"spec 必须包含/排除某资源"变成机器断言。
§11.3 只允许**新增页面**进 QML，因此这里也顺带钉住：**不得**把存量 QWidget 视图
改写成 QML（那属于"扩大迁移"，其触发条件不成立）。
"""

from __future__ import annotations

import ast
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


def test_no_portable_spec_ships_the_qml_page_or_runtime() -> None:
    """2026-09-23 拍板：发布包不打包 QML——页面文件与运行时模块都不得进 spec。"""
    for relative in _SPECS:
        text = (_REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "omnicrawler/gui/qml" not in text, (
            f"{relative} 仍收集 QML 页面目录——拍板是『保留代码、不打包』，"
            "若要恢复打包须先经维护者撤回拍板并同步本守卫。"
        )
        for module in ("PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets"):
            assert module in text, f"{relative} 的 excludes 缺 {module}（QML 运行库 ≈45 MB 必须排除）"


def test_only_the_qml_pilot_page_uses_qtquick() -> None:
    """§11.3：QML 只用于**新增**的橱窗页 —— 存量 QWidget 视图不得被改写。

    判据：`QQuickWidget` 只允许出现在试点页与其测试里（按文件白名单）。
    """
    allowed = {
        "src/omnicrawler/gui/views/qml_showcase.py",
    }
    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / "src" / "omnicrawler" / "gui").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(_REPO_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法错误由别的门禁负责
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.endswith(("QtQuickWidgets", "QtQml", "QtQuick")) for name in names):
                if relative not in allowed:
                    offenders.append(relative)
                break
    assert offenders == [], (
        f"QML 运行时引用超出了试点范围：{offenders}。"
        "§11.3 只允许新增橱窗页用 QML；要把存量页面迁过去属于『扩大迁移』，"
        "需先满足其两个触发条件（见《优化方案》§11.3）。"
    )
