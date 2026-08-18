"""B05-019：AI 外发隐私闸门接线测试（fail-closed）。

覆盖 4 个接线落点：require_ai_privacy helper、OpenAICompatibleProvider.check_content_allowed、
natural_language_task.compile_with_ai、AIGraphExtractor._extract_chunk、AdaptiveExtractor。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.ai_env import require_ai_privacy, save_ai_config_sidecar
from omnicrawler.core.config import load_config
from omnicrawler.core.errors import AIPrivacyBlockedError


def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: b5019, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    return config_path


# ── 1. require_ai_privacy 基元 ───────────────────────────────────────


def test_require_ai_privacy_default_fail_closed(tmp_path: Path) -> None:
    """无 sidecar 时默认拒绝（DEFAULT_AI_PRIVACY 全 False）。"""
    with pytest.raises(AIPrivacyBlockedError, match="privacy.allow_page_text"):
        require_ai_privacy(tmp_path, content_kind="allow_page_text", what="测试内容")


def test_require_ai_privacy_allows_when_sidecar_enabled(tmp_path: Path) -> None:
    """sidecar 显式开启后放行。"""
    save_ai_config_sidecar(tmp_path, {"privacy": {"allow_pdf_content": True}})
    require_ai_privacy(tmp_path, content_kind="allow_pdf_content", what="PDF 正文")


# ── 2. OpenAICompatibleProvider.check_content_allowed ────────────────


def _provider(config_path: Path):
    from omnicrawler.services.ai_providers import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        "default",
        {"base_url": "https://api.example.com/v1", "api_key": "sk-x", "model": "m"},
        app_config=load_config(config_path),
    )


def test_provider_check_missing_app_config_blocks() -> None:
    from omnicrawler.services.ai_providers import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        "default",
        {"base_url": "https://api.example.com/v1", "api_key": "sk-x", "model": "m"},
    )
    with pytest.raises(AIPrivacyBlockedError):
        provider.check_content_allowed("allow_page_text", "页面内容")


def test_provider_check_blocks_by_default(tmp_path: Path) -> None:
    provider = _provider(_config(tmp_path))
    with pytest.raises(AIPrivacyBlockedError, match="privacy.allow_page_text"):
        provider.check_content_allowed("allow_page_text", "页面内容")


def test_provider_check_allows_when_enabled(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir(exist_ok=True)
    save_ai_config_sidecar(workspace, {"privacy": {"allow_page_text": True}})
    provider = _provider(_config(tmp_path))
    provider.check_content_allowed("allow_page_text", "页面内容")  # 不抛


# ── 3. natural_language_task.compile_with_ai ─────────────────────────


def test_compile_with_ai_blocks_without_privacy() -> None:
    """真实 provider（带 check_content_allowed）未开启 privacy 时 compile_with_ai 拒发。"""
    import tempfile

    from omnicrawler.services.ai_providers import OpenAICompatibleProvider
    from omnicrawler.services.natural_language_task import compile_with_ai

    with tempfile.TemporaryDirectory() as td:
        provider = OpenAICompatibleProvider(
            "default",
            {"base_url": "https://api.example.com/v1", "api_key": "sk-x", "model": "m"},
            app_config=load_config(_config(Path(td))),
        )
        with pytest.raises(AIPrivacyBlockedError):
            compile_with_ai("抓取 https://example.com/list 的公告", provider)


def test_compile_with_ai_passes_when_privacy_enabled() -> None:
    """sidecar 开启后 compile_with_ai 放行，且不阻塞正常解析。"""
    import tempfile
    from types import SimpleNamespace

    from omnicrawler.services.ai_providers import OpenAICompatibleProvider
    from omnicrawler.services.natural_language_task import compile_with_ai

    text = (
        '{"known_requirements": {"url": "https://example.com/list", '
        '"intent": "collect_section", "topics": ["公告"]}, '
        '"assumptions": [], "unresolved_questions": [], '
        '"explanations": [], "risks": [], "recommended_actions": [], "config_patch": {}}'
    )

    class _AllowProvider(OpenAICompatibleProvider):
        def generate(self, messages, **kwargs):  # noqa: D102
            return SimpleNamespace(text=text)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "work").mkdir(exist_ok=True)
        save_ai_config_sidecar(root / "work", {"privacy": {"allow_page_text": True}})
        provider = _AllowProvider(
            "default",
            {"base_url": "https://api.example.com/v1", "api_key": "sk-x", "model": "m"},
            app_config=load_config(_config(root)),
        )
        draft = compile_with_ai("抓取 https://example.com/list 的公告", provider)
        assert draft.task


# ── 4. AIGraphExtractor._extract_chunk ───────────────────────────────


def test_ai_graph_extract_blocks_without_privacy() -> None:
    from omnicrawler.extraction.ai_graph import AIGraphExtractor, FieldDef, Provider

    ex = AIGraphExtractor(provider=Provider(api_key="sk-test"))
    with pytest.raises(AIPrivacyBlockedError):
        # 不实际发请求：privacy 闸门在构造 payload 前即拦截
        import asyncio

        asyncio.run(ex._extract_chunk("<html>t</html>", [FieldDef(name="t")], 1000, session=object()))


# ── 5. AdaptiveExtractor 真实 provider 路径 ──────────────────────────


def test_adaptive_extractor_blocks_without_privacy(monkeypatch) -> None:
    from omnicrawler.extraction.adaptive_extractor import AdaptiveExtractor
    from omnicrawler.services.ai_providers import OpenAICompatibleProvider

    class _BlockingProvider(OpenAICompatibleProvider):
        def generate(self, messages, **kwargs):  # noqa: D102
            raise AssertionError("不应外发")

    provider = _BlockingProvider(
        "default",
        {"base_url": "https://api.example.com/v1", "api_key": "sk-x", "model": "m"},
    )
    monkeypatch.setattr("omnicrawler.services.ai_providers.provider_from_env", lambda **k: provider)
    extractor = AdaptiveExtractor()  # llm_generate 未注入 → 走真实 provider 路径
    # 缺 app_config → check_content_allowed 抛 AIPrivacyBlockedError → _generate_rule 捕获返回 ""
    result = extractor._generate_rule("title", "css", "<html>x</html>")
    assert result == ""
