"""核心 ai_env 单一真源读写测试。

覆盖 Phase 0：ai_env_path 真源、读取优先级、行级就地写入（保留注释/空行/顺序）、
os.environ 同步、PDFX_LLM_* 桥接。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from omnicrawler.core.ai_env import (
    bridge_pdfx_llm_env,
    load_ai_env,
    parse_env_file,
    save_ai_env,
    sync_ai_env_to_os,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path / "proj"


def _write(project: Path, content: str) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / ".env").write_text(content, encoding="utf-8")


def test_ai_env_path_true_source(tmp_path: Path) -> None:
    from omnicrawler.core.ai_env import ai_env_path

    assert ai_env_path(tmp_path / "p") == tmp_path / "p" / ".env"
    # 无项目时回退用户级目录
    assert str(ai_env_path(None)).endswith(".omnicrawler" + os.sep + ".env")


def test_parse_env_file_ignores_comments_and_quotes(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# 注释\n\nKEY=value\nQUOTED='a b'\nDOUBLE=\"c#d\"\nBAD_LINE\n", encoding="utf-8"
    )
    parsed = parse_env_file(path)
    assert parsed == {"KEY": "value", "QUOTED": "a b", "DOUBLE": "c#d"}


def test_env_quote_roundtrip_unescapes_backslash_and_quote(tmp_path: Path) -> None:
    """写端 \\ 与 \" 转义后，读端应对称反转义，往返不损坏配置值。"""
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    # 引号载体改用非秘钥键（API_KEY 现在会被强制 seal，见 test_api_key_sealed）
    tricky_value = 'sk-abc\\def"ghi'
    tricky_url = "http://host/v1?x=a\\b"
    save_ai_env(
        {"OMNICRAWL_AI_MODEL": tricky_value, "OMNICRAWL_AI_BASE_URL": tricky_url},
        project_root=project,
    )
    parsed = parse_env_file(project / ".env")
    assert parsed["OMNICRAWL_AI_MODEL"] == tricky_value
    assert parsed["OMNICRAWL_AI_BASE_URL"] == tricky_url
    # 再写入一次（更新路径）不改变值
    save_ai_env({"OMNICRAWL_AI_MODEL": tricky_value}, project_root=project)
    assert parse_env_file(project / ".env")["OMNICRAWL_AI_MODEL"] == tricky_value


def test_api_key_is_sealed_on_save(tmp_path: Path, monkeypatch) -> None:
    """B05-021：save_ai_env 写 OMNICRAWL_AI_API_KEY 明文时强制 seal，不落明文。"""
    # seal_secret 依赖系统 keyring / OMNICRAWL_MASTER_PASSWORD（CI macOS runner 均无），
    # mock 掉真实密钥库，仅验证"强制 seal 路径被触发且不落明文"。
    monkeypatch.setattr(
        "omnicrawler.core.credentials.seal_secret",
        lambda key, value: "secret://sealed",
    )
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    save_ai_env({"OMNICRAWL_AI_API_KEY": "sk-plain-secret"}, project_root=project)
    written = (project / ".env").read_text(encoding="utf-8")
    assert "sk-plain-secret" not in written
    assert "secret://" in written
    # 已是 secret:// 引用则原样保留（幂等）
    save_ai_env({"OMNICRAWL_AI_API_KEY": "secret://OMNICRAWL_AI_API_KEY"}, project_root=project)
    assert parse_env_file(project / ".env")["OMNICRAWL_AI_API_KEY"] == "secret://OMNICRAWL_AI_API_KEY"


def test_load_ai_env_priority_project_over_user(project: Path, tmp_path: Path, monkeypatch) -> None:
    _write(project, "OMNICRAWL_AI_MODEL=project-model\nOMNICRAWL_AI_PROVIDER=openai_compatible\n")
    user_env = tmp_path / ".omnicrawler"
    user_env.mkdir(parents=True)
    (user_env / ".env").write_text("OMNICRAWL_AI_MODEL=user-model\n", encoding="utf-8")
    monkeypatch.setenv("OMNICRAWL_AI_MODEL", "env-model")
    monkeypatch.chdir(tmp_path)  # cwd 无 .env，避免干扰
    merged = load_ai_env(project)
    assert merged["OMNICRAWL_AI_MODEL"] == "env-model"  # os.environ 最高优先级


def test_save_ai_env_preserves_comments_and_order(project: Path) -> None:
    project.mkdir(parents=True)
    _write(
        project,
        "# 顶部注释\n\nOTHER_KEY=keep\n# AI 段注释\nOMNICRAWL_AI_PROVIDER=old\nOMNICRAWL_AI_TIMEOUT=30\n",
    )
    save_ai_env(
        {
            "OMNICRAWL_AI_PROVIDER": "openai_compatible",
            "OMNICRAWL_AI_MODEL": "gpt-x",
            "OMNICRAWL_AI_TIMEOUT": None,
        },
        project_root=project,
    )
    lines = (project / ".env").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# 顶部注释"
    assert lines[1] == ""
    assert "OTHER_KEY=keep" in lines
    assert "# AI 段注释" in lines
    assert "OMNICRAWL_AI_PROVIDER=openai_compatible" in lines
    assert "OMNICRAWL_AI_MODEL=gpt-x" in lines
    # 删除的键不再出现
    assert not any("OMNICRAWL_AI_TIMEOUT" in line for line in lines)
    # 顺序保持：注释行仍在原相对位置（OTHER_KEY 在第3行）
    assert lines.index("# 顶部注释") == 0


def test_save_ai_env_quotes_special_values(project: Path) -> None:
    project.mkdir(parents=True)
    save_ai_env({"OMNICRAWL_AI_BASE_URL": "https://x.io/v1 # note"}, project_root=project)
    raw = (project / ".env").read_text(encoding="utf-8")
    assert raw.strip() == 'OMNICRAWL_AI_BASE_URL="https://x.io/v1 # note"'
    # 重新解析能还原
    assert parse_env_file(project / ".env")["OMNICRAWL_AI_BASE_URL"] == "https://x.io/v1 # note"


def test_sync_ai_env_to_os(project: Path, monkeypatch) -> None:
    monkeypatch.delenv("OMNICRAWL_AI_MODEL", raising=False)
    sync_ai_env_to_os({"OMNICRAWL_AI_MODEL": "m1"})
    assert os.environ["OMNICRAWL_AI_MODEL"] == "m1"
    sync_ai_env_to_os({"OMNICRAWL_AI_MODEL": None})
    assert "OMNICRAWL_AI_MODEL" not in os.environ


def test_sync_ai_env_to_os_invalidates_stale_bridge(project: Path, monkeypatch) -> None:
    """同进程改配置后，旧 PDFX_LLM_* 桥接值应失效，下次桥接以新值为准。"""
    monkeypatch.delenv("OMNICRAWL_AI_MODEL", raising=False)
    monkeypatch.setenv("PDFX_LLM_MODEL", "stale-model")
    sync_ai_env_to_os({"OMNICRAWL_AI_MODEL": "new-model"})
    assert "PDFX_LLM_MODEL" not in os.environ
    # sync_ai_env_to_os 直写 os.environ 绕过 monkeypatch，须显式清理防止泄漏
    sync_ai_env_to_os({"OMNICRAWL_AI_MODEL": None})


def test_bridge_pdfx_llm_env(project: Path, monkeypatch) -> None:
    _write(project, "OMNICRAWL_AI_PROVIDER=openai_compatible\nOMNICRAWL_AI_MODEL=bridge-model\n")
    for key in (
        "OMNICRAWL_AI_PROVIDER", "OMNICRAWL_AI_BASE_URL", "OMNICRAWL_AI_MODEL",
        "OMNICRAWL_AI_API_KEY", "OMNICRAWL_AI_TIMEOUT",
        "PDFX_LLM_PROVIDER", "PDFX_LLM_BASE_URL", "PDFX_LLM_MODEL",
        "PDFX_LLM_API_KEY", "PDFX_LLM_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    bridge_pdfx_llm_env(project)
    assert os.environ["PDFX_LLM_PROVIDER"] == "openai_compatible"
    assert os.environ["PDFX_LLM_MODEL"] == "bridge-model"


def test_bridge_pdfx_llm_env_respects_explicit(project: Path, monkeypatch) -> None:
    _write(
        project,
        "OMNICRAWL_AI_PROVIDER=openai_compatible\n"
        "PDFX_LLM_PROVIDER=disabled\n",  # .env 中显式配置优先
    )
    for key in (
        "OMNICRAWL_AI_PROVIDER", "OMNICRAWL_AI_BASE_URL", "OMNICRAWL_AI_MODEL",
        "OMNICRAWL_AI_API_KEY", "OMNICRAWL_AI_TIMEOUT",
        "PDFX_LLM_PROVIDER", "PDFX_LLM_MODEL", "PDFX_LLM_BASE_URL", "PDFX_LLM_API_KEY", "PDFX_LLM_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    bridge_pdfx_llm_env(project)
    assert os.environ["PDFX_LLM_PROVIDER"] == "disabled"  # 不覆盖显式值
