"""pdfx 装载层 AI 环境桥接测试（Phase 0）。

验证从任意 cwd 调用 load_config 时，桥接能定位项目根 .env
（GUI 写入真源），使 CLI/headless 路径的 PDFX_LLM_* 模板展开一致生效。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from omnicrawl.pdfx.config import load_config
from omnicrawl.pdfx.templates import builtin_pdf_resource


def test_load_config_bridges_project_ai_env_from_non_cwd(tmp_path: Path, monkeypatch) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    config_dir = proj / "configs" / "pdf"
    config_dir.mkdir(parents=True)
    shutil.copy(builtin_pdf_resource("generic_template.yaml"), config_dir / "test.yaml")

    (proj / ".env").write_text(
        "OMNICRAWL_AI_PROVIDER=openai_compatible\n"
        "OMNICRAWL_AI_BASE_URL=http://127.0.0.1:11434/v1\n"
        "OMNICRAWL_AI_MODEL=qwen2.5\n"
        "OMNICRAWL_AI_API_KEY=sk-test\n",
        encoding="utf-8",
    )

    # 运行在无关目录，模拟 CLI 从任意 cwd 启动；清除进程级显式值走桥接
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    for key in (
        "OMNICRAWL_AI_PROVIDER", "OMNICRAWL_AI_BASE_URL", "OMNICRAWL_AI_MODEL",
        "OMNICRAWL_AI_API_KEY", "OMNICRAWL_AI_TIMEOUT",
        "PDFX_LLM_PROVIDER", "PDFX_LLM_BASE_URL", "PDFX_LLM_MODEL",
        "PDFX_LLM_API_KEY", "PDFX_LLM_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)

    config = load_config(config_dir / "test.yaml")
    assert config.llm["provider"] == "openai_compatible"
    assert config.llm["model"] == "qwen2.5"
    assert config.llm["base_url"] == "http://127.0.0.1:11434/v1"
    assert config.llm["api_key"] == "sk-test"
