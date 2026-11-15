"""条目 2：GUI「自动」OCR 后端不得静默退化为「无后端」。

缺陷链路（修复前）：
    CrawlConfig.pdf_ocr == "auto"（默认值）
      → to_yaml 写 ``ocr_backend: none``（config_serializer.py:178）
      → pdfx 读到 backend="none" → create_backend 返回 None
      → ocr_stage 把 needs_ocr 页全部标 skipped，无 error、无提示
      → 用户以为做了 OCR，导出的文档里没有文字

本文件的判据是**行为可观察**，不是查配置字面量：
「自动」必须解析出一个真实后端，或在确实没有后端时给出可见原因；
不得出现「配置说是自动、运行期什么也不做、还不告诉用户」这一组合。

反向断言要求：修复前这些用例必须红。
"""
from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("ruamel") is None,
    reason="需要 GUI 配置序列化（ruamel.yaml）",
)


def _auto_config():
    from omnicrawler.gui.core.config_model import CrawlConfig

    config = CrawlConfig(project_name="auto_ocr", workspace="work/auto_ocr")
    config.pdf_ocr = "auto"
    return config


def _serialized_pdf_block() -> dict:
    """走完整链路：CrawlConfig → to_yaml → yaml.safe_load → processors.pdf。"""
    import yaml

    from omnicrawler.gui.core.config_serializer import to_yaml

    document = yaml.safe_load(to_yaml(_auto_config()))
    return document["processors"]["pdf"]


def test_auto_ocr_is_not_serialized_as_none() -> None:
    """「自动」不得被折叠成 none —— 折叠后等同于「关闭 OCR」。"""
    pdf = _serialized_pdf_block()
    assert pdf["skip_ocr"] is False, "自动模式不应跳过 OCR"
    assert pdf["ocr_backend"] != "none", (
        "pdf_ocr='auto' 被序列化成 ocr_backend='none'："
        "「自动」实际等于「没有 OCR」，扫描页会被静默跳过"
    )


def test_auto_ocr_round_trip_preserves_auto_semantics() -> None:
    """自动模式的往返必须保持「自动」语义，不能被固化成某台机器的后端。"""
    yaml_text = None
    from omnicrawler.gui.core.config_serializer import from_yaml, to_yaml

    yaml_text = to_yaml(_auto_config())
    restored = from_yaml(yaml_text)
    assert restored.pdf_ocr == "auto", (
        f"往返后 pdf_ocr={restored.pdf_ocr!r}，丢掉了「自动」语义"
    )


def test_auto_backend_resolves_to_a_real_engine_or_says_why() -> None:
    """自动模式必须落到真实后端，或给出「为什么没有」的可见原因。

    允许两种正确形态：
      a) 解析出 paddle / tesseract（本机有就绪后端）；
      b) 明确声明不可用，并带有可读原因。
    不允许：返回 none 却对外宣称「自动」。
    """
    from omnicrawler.pdfx.ocr import resolve_auto_ocr_backend

    resolved, reason = resolve_auto_ocr_backend()
    assert resolved in {"paddle", "tesseract", "none"}, resolved
    if resolved == "none":
        assert reason, "解析不出后端时必须给出原因，否则用户无法知道该怎么办"
        assert "自动" not in reason or "未检测" in reason or "安装" in reason, (
            f"原因不可操作：{reason!r}"
        )
