"""条目 3.2：PDF 工作台的 OCR 后端改为**按实际可用性解析**，不再写死 paddle。

修复前：``pdf_workbench`` 建项目时硬写 ``ocr_backend="paddle"``，
导致两个真实问题——

1. Standard 版用户即使正确安装了 OCR 组件，**从 GUI 走也永远用不上**
   （``ocr_component`` 从未传进 ``create_project_config``）；
2. 只有 Tesseract 的机器被判「OCR 不可用」，尽管它本可以正常识别扫描件。

本文件钉住解析顺序与降级判据。判据是**行为**，不是查字符串：
给定"哪些后端可用"，必须得到确定的后端与组件。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("PySide6", reason="GUI 后端解析需要 PySide6") is None,
    reason="需要 PySide6",
)


class _FakeWorkbench:
    """只带 ``_pick_installed_component`` 的替身，用于隔离宿主依赖探测。"""

    def __init__(self, component: str) -> None:
        self._component = component

    _pick_installed_component = lambda self: self._component  # noqa: E731


def _resolve(monkeypatch: pytest.MonkeyPatch, *, component: str,
             paddle: bool, tesseract: bool) -> tuple[str, str, str]:
    """调用真实的 ``PdfWorkbenchView._resolve_ocr_backend``，依赖用 monkeypatch 替掉。"""
    import builtins

    from omnicrawler.gui.views.pdf_workbench import PdfWorkbenchView
    from omnicrawler.pdfx import ocr as ocr_module

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object):
        if name == "paddleocr" and not paddle:
            raise ImportError("No module named 'paddleocr'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.setattr(ocr_module, "_tesseract_ready", lambda: tesseract)

    fake = _FakeWorkbench(component)
    return PdfWorkbenchView._resolve_ocr_backend(fake)  # type: ignore[arg-type]


def test_installed_component_wins_over_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """有受信组件就用组件 —— 即使宿主也装了 Paddle。

    这是修复的核心：组件自带运行时、已验签，不该被宿主依赖抢走。
    """
    backend, component, reason = _resolve(
        monkeypatch, component="ocr-tesseract", paddle=True, tesseract=True
    )
    assert backend == "paddle", "组件运行协议要求声明引擎；组件优先"
    assert component == "ocr-tesseract"
    assert reason == ""


def test_component_works_even_without_paddle_or_tesseract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只有组件、宿主什么都没有 ⇒ 仍然可用（Standard 版的关键路径）。"""
    backend, component, _ = _resolve(
        monkeypatch, component="ocr-standalone", paddle=False, tesseract=False
    )
    assert component == "ocr-standalone"
    assert backend != "none"


def test_paddle_used_when_no_component(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, component, _ = _resolve(
        monkeypatch, component="", paddle=True, tesseract=False
    )
    assert backend == "paddle"
    assert component == ""


def test_tesseract_fallback_when_only_tesseract_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只有 Tesseract 时必须选它，而不是判"不可用"。

    修复前这里会走 ``import paddleocr`` 失败分支，直接把用户的 OCR 关掉。
    """
    backend, _, _ = _resolve(monkeypatch, component="", paddle=False, tesseract=True)
    assert backend == "tesseract"


def test_none_returns_an_actionable_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """三者都不可用时，原因必须可操作（告诉用户装什么 / 或改用文字版 PDF）。"""
    backend, component, reason = _resolve(
        monkeypatch, component="", paddle=False, tesseract=False
    )
    assert backend == "none"
    assert component == ""
    assert reason, "必须给出原因，不能静默关闭 OCR"
    assert ("组件" in reason) or ("文字版" in reason), (
        f"原因不可操作：{reason!r}——用户看完不知道该做什么"
    )


def test_component_discovery_failure_is_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """组件注册表读不出来 ⇒ 视为"没有组件"，不得拦住工作台。"""
    from omnicrawler.gui.views.pdf_workbench import PdfWorkbenchView

    def _boom() -> object:
        raise OSError("registry locked")

    monkeypatch.setattr("omnicrawler.commands.components.manager", _boom)
    fake = _FakeWorkbench("")
    # 直接调真实实现（不走替身）
    assert PdfWorkbenchView._pick_installed_component(fake) == ""  # type: ignore[arg-type]
