"""自然语言任务解析修复测试（Phase 1：C19/C20/C21/C23/C24/C25/C27/E12）。"""

from __future__ import annotations

import pytest

from omnicrawl.services.ai_safety import AISafetyViolationError
from omnicrawl.services.natural_language_task import compile_natural_language, compile_with_ai
from omnicrawl.services.ux_service import draft_quick_task


class _FakeProvider:
    """模拟 AI provider：返回预设文本，并记录调用参数。"""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    def generate(self, messages, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"text": self._text})()


def test_c19_intent_field_is_read() -> None:
    """AI 输出的 known_requirements.intent 必须生效，而非恒退化 save_page。"""
    text = (
        '{"known_requirements": {"url": "https://example.com/list", '
        '"intent": "collect_section", "topics": ["公告"]}, '
        '"assumptions": [], "unresolved_questions": [], '
        '"explanations": [], "risks": [], "recommended_actions": [], "config_patch": {}}'
    )
    draft = compile_with_ai("抓取 https://example.com/list 的公告列表", _FakeProvider(text))
    assert draft is not None
    assert draft.task.intent == "collect_section"
    assert draft.task.max_pages == 30


def test_c19_fallback_for_unknown_intent() -> None:
    """不支持的 intent 降级为 save_page，不整体作废（C20）。"""
    text = (
        '{"known_requirements": {"url": "https://example.com/x", '
        '"intent": "fly_to_moon"}, '
        '"assumptions": [{"field": "url", "value": "https://example.com", '
        '"reason": "r", "confidence": "low"}], "unresolved_questions": [], '
        '"explanations": [], "risks": [], "recommended_actions": [], "config_patch": {}}'
    )
    draft = compile_with_ai("保存 https://example.com/x", _FakeProvider(text))
    assert draft is not None
    assert draft.task.intent == "save_page"
    assert draft.task.url == "https://example.com/x"  # 降级保留合法 url
    assert draft.ai_assumptions  # 降级不丢假设


def test_c20_invalid_url_falls_back_not_raise() -> None:
    """AI 返回非法 url 时应降级而非整体作废（should-fix 回归）。"""
    text = (
        '{"known_requirements": {"url": "not-a-valid-url", '
        '"intent": "save_page"}, '
        '"assumptions": [], "unresolved_questions": [], '
        '"explanations": [], "risks": [], "recommended_actions": [], "config_patch": {}}'
    )
    draft = compile_with_ai("保存这个页面", _FakeProvider(text))
    assert draft is not None
    assert draft.task.url.startswith("http")


def test_c21_topics_filters_empty_strings() -> None:
    text = (
        '{"known_requirements": {"url": "https://example.com/x", '
        '"intent": "save_page", "topics": []}, '
        '"assumptions": [], "unresolved_questions": [], '
        '"explanations": [], "risks": [], "recommended_actions": [], "config_patch": {}}'
    )
    draft = compile_with_ai("保存 https://example.com/x", _FakeProvider(text))
    assert draft is not None
    assert draft.topics == ()


def test_c23_pdf_quoted_topic_full_text() -> None:
    """PDF 分支引号内主题词取完整文本，而非首字符。"""
    draft = compile_natural_language("提取 C:/data/2024年报.pdf 中“担保金额”相关内容")
    assert draft.mode == "pdf"
    assert "担保金额" in draft.topics


def test_e12_curly_quotes_in_pdf_mode() -> None:
    draft = compile_natural_language("分析 D:/docs/公告.pdf 中『关联交易』")
    assert draft.mode == "pdf"
    assert "关联交易" in draft.topics


def test_c24_ftp_url_not_misread_as_local_path() -> None:
    """ftp:// 不应被 _FILE_PATH 截出 p://host/x 当本地盘符路径。"""
    draft = compile_natural_language("从 ftp://files.example.com/share/report.pdf 下载文件")
    assert all(not p.startswith("p:/") for p in draft.file_paths)
    # 本地盘符路径仍正常识别
    draft2 = compile_natural_language("提取 C:/data/2024年报.pdf 中内容")
    assert any(str(p).startswith("C:/") for p in draft2.file_paths)


def test_c25_safety_violation_uses_dedicated_exception() -> None:
    """AI 试图扩大入口域名时抛专用异常，调用方可明示"已拦截越权建议"。"""
    text = (
        '{"known_requirements": {"url": "https://example.com/x", "intent": "save_page"}, '
        '"assumptions": [], "unresolved_questions": [], "explanations": [], '
        '"risks": [], "recommended_actions": [], '
        '"config_patch": {"seed_urls": ["https://evil.example.org/steal"]}}'
    )
    with pytest.raises(AISafetyViolationError) as excinfo:
        compile_with_ai("保存 https://example.com/x", _FakeProvider(text))
    assert excinfo.value.violations
    assert any("扩大" in v for v in excinfo.value.violations)
    # 兼容既有 except ValueError 调用方
    assert isinstance(excinfo.value, ValueError)


def test_c27_response_format_json_object_is_requested() -> None:
    """必须显式请求 json_object，降低模型返回 Markdown 围栏的概率。"""
    text = (
        '{"known_requirements": {"url": "https://example.com/x", "intent": "save_page"}, '
        '"assumptions": [], "unresolved_questions": [], "explanations": [], '
        '"risks": [], "recommended_actions": [], "config_patch": {}}'
    )
    provider = _FakeProvider(text)
    compile_with_ai("保存 https://example.com/x", provider)
    assert provider.calls[0]["response_format"] == {"type": "json_object"}


def test_draft_quick_task_intent_whitelist() -> None:
    task = draft_quick_task("https://example.com", "monitor_changes")
    assert task.monitor_changes is True
