"""验证码 OCR — 基于 ddddocr 的轻量级验证码识别。

使用方式:
    recognizer = CaptchaRecognizer()
    result = recognizer.recognize(image_bytes)  # 返回识别文本

可选 extras: omnicrawl[ocr-captcha]
"""

from __future__ import annotations

import threading
from typing import Any


class CaptchaRecognizer:
    """ddddocr 封装 — 专门针对中英文验证码场景。"""

    def __init__(self) -> None:
        self._ocr: Any = None
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                import ddddocr  # noqa: F401
                import onnxruntime  # noqa: F401
                self._available = True
            except ImportError:
                self._available = False
        return self._available

    def _ensure_ocr(self) -> Any:
        if not self.available:
            raise RuntimeError(
                "ddddocr 未安装，请执行 pip install omnicrawl[ocr-captcha]"
            )
        if self._ocr is None:
            import ddddocr
            import onnxruntime
            onnxruntime.set_default_logger_severity(3)  # 抑制 ONNX 日志
            self._ocr = ddddocr.DdddOcr(show_ad=False)
        return self._ocr

    def recognize(self, image: bytes | str) -> str:
        """识别验证码图片，返回文本。

        Args:
            image: PNG/JPEG 图片 bytes，或图片文件路径。

        Returns:
            识别出的文本字符串。
        """
        if isinstance(image, str):
            with open(image, "rb") as fh:
                image = fh.read()
        ocr = self._ensure_ocr()
        result = ocr.classification(image)
        return str(result) if result else ""

    def recognize_from_screenshot(self, page: Any, selector: str) -> str:
        """从 Playwright 页面元素截图中识别验证码。

        Args:
            page: Playwright Page 对象。
            selector: 验证码图片的 CSS/XPath 选择器。

        Returns:
            识别出的文本。
        """
        element = page.query_selector(selector)
        if element is None:
            raise ValueError(f"未找到验证码元素: {selector}")
        screenshot = element.screenshot()
        return self.recognize(screenshot)


# 全局单例
_captcha: CaptchaRecognizer | None = None
_lock = threading.Lock()


def get_captcha_recognizer() -> CaptchaRecognizer:
    global _captcha
    if _captcha is None:
        with _lock:
            if _captcha is None:
                _captcha = CaptchaRecognizer()
    return _captcha
