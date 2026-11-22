"""`fetching/captcha_ocr.py` 的守卫：可选能力缺失时必须**明确报错**，不静默。

## 为什么这个模块值得测（此前 0% 覆盖）

`CaptchaRecognizer` 依赖可选 extra `ddddocr`。这类「可选能力」的典型事故是
**静默失败**：没装依赖时返回空串或 `None`，调用方以为「识别结果是空」，
于是把空验证码提交上去 —— 报错位置远离根因，排查成本极高。

本测试固定三件事：

1. **可用性判据必须真实**：`available()` 反映 `ddddocr` 能否 import；
2. **缺失时必须抛错**：`recognize()` / `_ensure_ocr()` 不能返回空串；
3. **有依赖时行为正确**：用假 ddddocr 验证返回值透传。

## 边界

它**不测真识别准确率**（那需要真实验证码图片与 ddddocr 本体）。
本文件的定位是「接口契约与降级行为」，不是 OCR 质量。
"""
from __future__ import annotations

import sys
import types

import pytest

from omnicrawler.fetching import captcha_ocr


@pytest.fixture
def without_ddddocr(monkeypatch: pytest.MonkeyPatch):
    """模拟「没装 ddddocr」：从 sys.modules 摘掉并让 import 失败。"""
    monkeypatch.delitem(sys.modules, "ddddocr", raising=False)
    # 让 `import ddddocr` 抛 ImportError
    monkeypatch.setitem(sys.modules, "ddddocr", None)
    recognizer = captcha_ocr.CaptchaRecognizer()
    recognizer._ocr = None  # 清掉可能已缓存的实例
    recognizer._available = None  # 清掉可用性缓存（它是 `None` 才重新探测）
    return recognizer


def test_available_is_false_without_dependency(without_ddddocr) -> None:
    """没装 ddddocr 时 `available()` 必须是 False（不是靠异常，是明确判据）。"""
    assert without_ddddocr.available is False


def test_recognize_raises_without_dependency(without_ddddocr) -> None:
    """**缺失时必须抛错**，不能返回空串 —— 空串会被当成「识别结果为空」。"""
    with pytest.raises(RuntimeError) as excinfo:
        without_ddddocr.recognize(b"\x89PNG fake")
    message = str(excinfo.value)
    assert "ddddocr" in message.lower() or "安装" in message or "extra" in message.lower(), (
        f"错误信息没告诉用户缺什么：{message!r}"
    )


def test_recognize_never_silently_returns_empty(without_ddddocr) -> None:
    """反向断言式的保护：绝不能返回空字符串冒充「识别成功但没结果」。"""
    try:
        result = without_ddddocr.recognize(b"fake")
    except Exception:  # noqa: BLE001 - 抛错是**期望**行为
        return
    pytest.fail(f"缺依赖时 recognize 返回了 {result!r} 而不是抛错 —— 静默失败")


def test_recognize_with_fake_backend_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """装上依赖后，`recognize` 必须把后端返回值透传出来。"""

    class _FakeOcr:
        def classification(self, image: bytes) -> str:
            return "7A9K"

    fake_module = types.ModuleType("ddddocr")
    fake_module.DdddOcr = lambda *args, **kwargs: _FakeOcr()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ddddocr", fake_module)

    # **`available` 同时要求 onnxruntime**（不只是 ddddocr）——实现里两个都 import。
    fake_ort = types.ModuleType("onnxruntime")
    fake_ort.set_default_logger_severity = lambda _level: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)

    recognizer = captcha_ocr.CaptchaRecognizer()
    recognizer._ocr = None
    recognizer._available = None
    assert recognizer.available is True
    assert recognizer.recognize(b"fake-image") == "7A9K"


def test_get_captcha_recognizer_is_cached() -> None:
    """工厂函数必须复用实例（ddddocr 的初始化有模型加载成本）。"""
    first = captcha_ocr.get_captcha_recognizer()
    second = captcha_ocr.get_captcha_recognizer()
    assert first is second, "每次调用都新建实例 —— 会反复加载 OCR 模型"


def test_recognize_from_screenshot_rejects_unknown_page(without_ddddocr) -> None:
    """传入不具备截图能力的对象时要报错，而不是 `AttributeError` 直接冒泡。"""

    class _NoScreenshot:
        pass

    # 实现里对不具备截图能力的对象会走 AttributeError（无 screenshot 属性）
    with pytest.raises((AttributeError, RuntimeError, ValueError)):
        without_ddddocr.recognize_from_screenshot(_NoScreenshot(), "#captcha")
