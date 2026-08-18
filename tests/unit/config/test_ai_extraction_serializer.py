"""config_serializer AI extraction 四字段往返测试（Phase 1c / A4）。

回归：to_yaml/from_yaml 曾漏掉 extraction_mode / ai_extraction_prompt /
ai_chunk_strategy / ai_max_tokens_per_chunk，导致"AI 智能提取"存盘即丢。
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("ruamel") is None,
    reason="GUI YAML round-trip requires the optional ruamel.yaml dependency",
)


def test_ai_extraction_fields_round_trip() -> None:
    from omnicrawler.gui.core.config_serializer import from_yaml, to_yaml

    original = """
project: {name: demo, workspace: work/demo}
source:
  kind: crawl
  seeds: [https://example.com]
ai:
  mode: enabled
  default_provider: default
  providers:
    default: {type: openai_compatible, base_url: "http://127.0.0.1:11434/v1", model: qwen2.5}
  extraction:
    mode: hybrid
    prompt: "只提取担保金额与利率"
    chunk_strategy: heading
    max_tokens_per_chunk: 2000
"""
    config = from_yaml(original)
    assert config.extraction_mode == "hybrid"
    assert config.ai_extraction_prompt == "只提取担保金额与利率"
    assert config.ai_chunk_strategy == "heading"
    assert config.ai_max_tokens_per_chunk == 2000

    rendered = to_yaml(config)
    loaded = from_yaml(rendered)
    assert loaded.extraction_mode == "hybrid"
    assert loaded.ai_extraction_prompt == "只提取担保金额与利率"
    assert loaded.ai_chunk_strategy == "heading"
    assert loaded.ai_max_tokens_per_chunk == 2000


def test_ai_extraction_defaults_when_absent() -> None:
    from omnicrawler.gui.core.config_serializer import from_yaml

    original = """
project: {name: demo, workspace: work/demo}
source: {kind: crawl, seeds: [https://example.com]}
ai: {mode: disabled}
"""
    config = from_yaml(original)
    assert config.extraction_mode == "selector"
    assert config.ai_extraction_prompt is None
    assert config.ai_chunk_strategy == "auto"
    assert config.ai_max_tokens_per_chunk == 4000
