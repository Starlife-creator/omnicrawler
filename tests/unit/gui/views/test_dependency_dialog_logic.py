"""依赖对话框的**项提取**逻辑测试（决策三：原生阻断 / 插件不阻断）。

只测纯函数 ``dependency_install_items``：从预检报告里挑出可安装项并标注
``blocking``。Qt 对话框本身不在此测（需 offscreen，另由 GUI 套件覆盖）。
"""

from __future__ import annotations

from omnicrawler.gui.views.dependency_dialog import dependency_install_items


def _report(*checks: dict) -> dict:
    return {"checks": list(checks)}


class TestDependencyInstallItems:
    def test_native_missing_is_blocking(self) -> None:
        report = _report(
            {
                "status": "error",
                "title": "依赖：pdfplumber",
                "message": "未安装；pip install pdfplumber",
                "fix": {
                    "action": "install",
                    "requirement": "pdfplumber",
                    "dependency_class": "native",
                },
            }
        )
        items = dependency_install_items(report)
        assert len(items) == 1
        assert items[0]["requirement"] == "pdfplumber"
        assert items[0]["blocking"] is True

    def test_plugin_missing_is_not_blocking(self) -> None:
        report = _report(
            {
                "status": "warning",
                "title": "插件依赖：academic-paper-downloader",
                "message": "声明的依赖 playwright 未安装",
                "fix": {
                    "action": "install",
                    "requirement": "playwright",
                    "dependency_class": "plugin",
                },
            }
        )
        items = dependency_install_items(report)
        assert len(items) == 1
        assert items[0]["blocking"] is False

    def test_ok_checks_are_ignored(self) -> None:
        report = _report(
            {"status": "ok", "title": "依赖：PyYAML", "message": "已安装", "fix": None},
            {
                "status": "ok",
                "title": "磁盘空间",
                "message": "充足",
                "fix": {"action": "open_folder", "path": "/tmp"},
            },
        )
        assert dependency_install_items(report) == []

    def test_install_without_requirement_skipped(self) -> None:
        # 老式数据（只有 extra 文字提示、没有 requirement）不应被当成可安装项
        report = _report(
            {
                "status": "error",
                "title": "依赖：X",
                "message": "未安装；pip install X",
                "fix": {"action": "install", "extra": "pip install X"},
            }
        )
        assert dependency_install_items(report) == []

    def test_mixed_preserves_both_classes(self) -> None:
        report = _report(
            {
                "status": "error", "title": "原生", "message": "",
                "fix": {"action": "install", "requirement": "pdfplumber", "dependency_class": "native"},
            },
            {
                "status": "warning", "title": "插件", "message": "",
                "fix": {"action": "install", "requirement": "playwright", "dependency_class": "plugin"},
            },
        )
        items = dependency_install_items(report)
        assert {i["requirement"] for i in items} == {"pdfplumber", "playwright"}
        assert sum(1 for i in items if i["blocking"]) == 1

    def test_empty_report(self) -> None:
        assert dependency_install_items({}) == []
        assert dependency_install_items({"checks": []}) == []
