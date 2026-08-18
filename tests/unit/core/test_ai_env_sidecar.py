"""C36 / C37 回归：AI 完整配置旁路持久化 与 外发隐私开关读取。

- C36：privacy/budget/routing/extraction 经 `save_ai_config_sidecar` 落盘，
  `load_ai_config_sidecar` 回读；api_key 绝不写入明文 JSON。
- C37（B05-019）：`load_ai_privacy` 默认全禁止（fail-closed），显式开启项从旁路 JSON 读取。
"""

from __future__ import annotations

import json

from omnicrawler.core.ai_env import (
    ai_config_sidecar_path,
    load_ai_config_sidecar,
    load_ai_privacy,
    save_ai_config_sidecar,
)


def _write_env(tmp_path: object, content: str = "OMNICRAWL_AI_PROVIDER=disabled\n") -> str:
    env = tmp_path / ".env"  # type: ignore[attr-defined]
    env.write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_c36_sidecar_roundtrip_persists_non_secret(tmp_path) -> None:
    root = _write_env(tmp_path)
    config = {
        "mode": "enabled",
        "providers": {
            "default": {
                "type": "openai_compatible",
                "base_url": "https://api.example.com",
                "model": "gpt-4o",
                "api_key": "SK-PLAINTEXT-MUST-NOT-PERSIST",
                "timeout_seconds": 30,
            }
        },
        "privacy": {"allow_page_text": True, "allow_pdf_content": False},
        "budget": {"max_tokens_per_request": 2048},
        "routing": {"field_designer": "default"},
    }
    path = save_ai_config_sidecar(root, config)

    data = json.loads(path.read_text(encoding="utf-8"))
    # 非机密字段保留
    assert data["privacy"]["allow_pdf_content"] is False
    assert data["budget"]["max_tokens_per_request"] == 2048
    assert data["routing"]["field_designer"] == "default"
    # C36：api_key 被剥离，绝不落明文
    assert "api_key" not in data
    assert data["providers"]["default"].get("api_key") is None


def test_c36_load_merges_sidecar_back(tmp_path) -> None:
    root = _write_env(tmp_path)
    save_ai_config_sidecar(
        root,
        {"privacy": {"allow_page_text": False}, "budget": {"max_tokens_per_request": 1024}},
    )
    loaded = load_ai_config_sidecar(root)
    assert loaded["privacy"]["allow_page_text"] is False
    assert loaded["budget"]["max_tokens_per_request"] == 1024


def test_c36_load_missing_returns_empty(tmp_path) -> None:
    root = _write_env(tmp_path)
    assert load_ai_config_sidecar(root) == {}


def test_c36_sidecar_path_along_env(tmp_path) -> None:
    root = _write_env(tmp_path)
    assert ai_config_sidecar_path(root).name == "ai_config.json"


def test_c37_privacy_defaults_fail_closed(tmp_path) -> None:
    """B05-019：无旁路 JSON 时隐私默认全部禁止（fail-closed），与 config 默认一致。"""
    root = _write_env(tmp_path)  # 无旁路 JSON
    privacy = load_ai_privacy(root)
    assert privacy == {
        "allow_page_text": False,
        "allow_pdf_content": False,
        "allow_screenshots": False,
        "allow_cookies": False,
    }


def test_c37_privacy_reads_explicit_enable(tmp_path) -> None:
    root = _write_env(tmp_path)
    save_ai_config_sidecar(root, {"privacy": {"allow_pdf_content": True, "allow_page_text": True}})
    privacy = load_ai_privacy(root)
    assert privacy["allow_pdf_content"] is True
    # 未显式设置的项回退默认（fail-closed：禁止）
    assert privacy["allow_page_text"] is True
    assert privacy["allow_cookies"] is False
