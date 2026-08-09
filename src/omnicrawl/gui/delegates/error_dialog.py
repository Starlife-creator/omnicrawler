"""Error dialog display with privacy redaction."""
from __future__ import annotations

import re
import traceback
from typing import Any

from PyQt6.QtWidgets import QApplication, QMessageBox

from ..i18n import _
from ._base import _BaseDelegate


class ErrorDialogHelper(_BaseDelegate):
    """Error dialog display with privacy redaction."""

    def show_error_dialog(self, exc: Exception, context: str = "", *, retry_callback: Any = None) -> None:
        """Show an error dialog with privacy redaction."""
        mw = self._mw
        tb = traceback.format_exc()
        redacted_tb = self.redact_error(tb)
        msg = QMessageBox(mw)
        msg.setWindowTitle(_("发生错误"))
        msg.setText(_("应用程序遇到一个错误: {0}").format(str(exc)[:200]))
        msg.setDetailedText(redacted_tb)
        if retry_callback is not None:
            retry_btn = msg.addButton(_("重试"), QMessageBox.ButtonRole.ActionRole)
            assert retry_btn is not None
            retry_btn.clicked.connect(lambda: (msg.close(), retry_callback()))
        help_btn = msg.addButton(_("查看帮助"), QMessageBox.ButtonRole.HelpRole)
        assert help_btn is not None
        copy_btn = msg.addButton(_("复制错误详情"), QMessageBox.ButtonRole.ActionRole)
        assert copy_btn is not None
        msg.addButton(_("关闭"), QMessageBox.ButtonRole.RejectRole)
        _clipboard = QApplication.clipboard()
        assert _clipboard is not None
        help_btn.clicked.connect(lambda: (msg.close(), mw._help_center.show_help("troubleshooting")))  # type: ignore[func-returns-value]
        copy_btn.clicked.connect(lambda: _clipboard.setText(
            _("# OmniCrawler GUI 错误报告\n# 配置中的网址和选择器已被隐藏以保护隐私\n# \n") + redacted_tb
        ))
        msg.exec()

    def redact_error(self, text: str) -> str:
        """Redact sensitive information from error text."""
        text = re.sub(r'https?://[^\s\'"<>]+', lambda m: re.sub(r'(https?://)[^/\s\'"<>]+', r'\1[REDACTED]', m.group(0)), text)
        # S3.1.14：只替换凭据上下文（key=/token=/password= 等后的参数值），
        # 普通句子中的问号不再被误伤
        text = re.sub(
            r'([?&](?:key|token|secret|password|passwd|signature|credential|auth|session|cookie)[=:])([^&\s\'"<>]*)',
            r'\1[REDACTED]',
            text,
            flags=re.I,
        )
        text = re.sub(r'(selector[=:]\s*["\'])([^"\']+)(["\'])', r'\1[REDACTED]\3', text)
        return text
