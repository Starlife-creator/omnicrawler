"""Help, about, shortcuts, and capability dialogs."""
from __future__ import annotations

import html
import tempfile

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QPalette
from PyQt6.QtWidgets import QApplication, QMessageBox

from ... import __version__ as APP_VERSION  # noqa: N812
from ..i18n import _
from ._base import _BaseDelegate


class HelpDialogManager(_BaseDelegate):
    """Help, about, shortcuts, and capability dialogs."""

    def show_selector_help(self) -> None:
        mw = self._mw
        from ...core.runtime_paths import package_resource
        help_path = package_resource("omnicrawl", "gui", "help", "selector_guide.html")
        if not help_path.is_file():
            QMessageBox.information(mw, _("帮助"), _("帮助文件未找到"))
            return
        html = help_path.read_text(encoding="utf-8")
        theme = mw._settings.theme
        if theme == "system":
            _app = QApplication.instance()
            assert isinstance(_app, QApplication)
            theme = "dark" if _app.palette().color(QPalette.ColorRole.Window).lightness() < 128 else "light"
        html = html.replace("<html lang=\"zh-CN\">", f'<html lang="zh-CN" data-theme="{theme}">')
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8")
        tmp.write(html)
        tmp.close()
        QDesktopServices.openUrl(QUrl.fromLocalFile(tmp.name))
        # S3.1.13：延迟删除临时文件（浏览器读取后清理），不再每次泄漏 tmp
        from pathlib import Path as _Path

        from PyQt6.QtCore import QTimer

        QTimer.singleShot(60_000, lambda: _Path(tmp.name).unlink(missing_ok=True))

    def show_quick_start(self) -> None:
        mw = self._mw
        from ...core.runtime_paths import find_document
        doc_path = find_document(_("OmniCrawler-用户指南.md"), "USER_GUIDE.md", "README.md")
        if doc_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(doc_path)))
        else:
            QMessageBox.information(mw, _("文档"), _("文档未找到"))

    def show_faq(self) -> None:
        mw = self._mw
        from ...core.runtime_paths import find_document
        doc_path = find_document("FAQ.md", _("OmniCrawler-用户指南.md"), "USER_GUIDE.md")
        if doc_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(doc_path)))
        else:
            QMessageBox.information(mw, _("文档"), _("文档未找到"))

    def show_shortcuts(self) -> None:
        mw = self._mw
        shortcuts = mw._settings.shortcuts
        text = _("快捷键列表:\n\n")
        for key, label in [("save", _("保存配置")), ("run", _("运行任务")), ("stop", _("停止任务")),
                           ("toggle_editor", _("切换向导/编辑器")), ("open_templates", _("打开模板库")),
                           ("refresh", _("刷新结果页")), ("format_yaml", _("格式化 YAML（编辑器模式）")),
                           ("toggle_dnd", _("切换请勿打扰模式"))]:
            text += f"  {shortcuts[key]:20s}  {label}\n"
        QMessageBox.information(mw, _("快捷键说明"), text)

    def show_about(self) -> None:
        mw = self._mw
        from ...core.runtime_paths import bundled_browser_available, is_frozen, portable_data_root
        version_info = mw._settings.omnicrawl_version or _("未知")
        runtime_mode = _("便携自包含") if is_frozen() else _("源码环境")
        browser_status = _("已就绪") if bundled_browser_available() else _("未随包提供")
        import sys as _sys
        # B10-002：版本/路径等动态值在富文本上下文中做 HTML 转义
        # （QMessageBox 受限富文本无 JS/远程加载，最坏视觉伪造，仍按规范处理）。
        text = (
            _("<h2>OmniCrawler GUI 工作台</h2>")
            + _("<p>版本: {0}</p>").format(html.escape(f"v{APP_VERSION}"))
            + _("<p>框架版本: {0}</p>").format(html.escape(version_info))
            + _("<p>运行模式: {0}<br>内置 Chromium: {1}<br>数据目录: {2}</p>").format(
                html.escape(runtime_mode),
                html.escape(browser_status),
                html.escape(str(portable_data_root())),
            )
            + _("<p>项目目录: {0}</p>").format(html.escape(str(mw._project_root)))
            + "<hr>"
            + _("<p>模块化网站采集与 PDF 数据抽取平台</p>")
            + f"<p>Python {_sys.version.split()[0]} | PyQt6</p>"
        )
        QMessageBox.about(mw, _("关于 OmniCrawler GUI"), text)

    def show_capabilities(self) -> None:
        mw = self._mw
        from ...core.capabilities import capability_report
        report = capability_report()
        module_lines = [f"{'✓' if item['installed'] else '✗'} {name}" for name, item in report["modules"].items()]
        native_lines = [_(f"{'✓' if item['ready'] else '✗'} {name}: {item.get('path') or '未找到'}") for name, item in report["native"].items()]
        QMessageBox.information(mw, _("运行能力与自包含组件"),
            _("Python 功能模块：\n") + "\n".join(module_lines)
            + _("\n\n本地运行组件：\n") + "\n".join(native_lines)
            + _("\n\n开发者可运行：omnicrawl capabilities --verify-imports"))
