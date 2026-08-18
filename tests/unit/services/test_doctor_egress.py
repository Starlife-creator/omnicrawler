"""S2.5.22：doctor 探测走 EgressBroker（策略约束，不探测私有目标）。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.services.doctor import _probe_models


def _config(tmp_path: Path, *, base_url: str) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        "project: {name: doctor, workspace: work}\n"
        f"source: {{kind: static_html, seeds: [https://example.org/]}}\n"
        f"ai: {{providers: {{default: {{base_url: {base_url!r}}}}}}}\n",
        encoding="utf-8",
    )
    return path


def test_probe_without_config_still_validates_url() -> None:
    result = _probe_models("not-a-url", "", "model")
    assert result["ok"] is False
    assert "base_url 无效" in result["detail"]


def test_probe_private_target_rejected_by_policy(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path, base_url="http://127.0.0.1:9999"))
    result = _probe_models("http://127.0.0.1:9999/v1", "key", "model", config=config)
    assert result["ok"] is False
    # 策略拦截（私有地址）而不是网络直连失败
    assert "探活失败" in result["detail"]


def test_probe_public_target_failure_is_graceful(tmp_path: Path, monkeypatch) -> None:
    import urllib.error

    from omnicrawler.fetching.http_client import build_safe_opener

    def _fake_opener(*_a, **_k):
        class _Opener:
            def open(self, *_a, **_k):
                raise urllib.error.URLError("mock network failure")

        return _Opener()

    # 用假 opener 替代真实 HTTP 探测，避免单元测试依赖 DNS/网络
    monkeypatch.setattr(build_safe_opener.__module__ + ".build_safe_opener", _fake_opener)
    config = load_config(_config(tmp_path, base_url="https://llm.example.invalid"))
    result = _probe_models("https://llm.example.invalid/v1", "key", "model", config=config)
    assert result["ok"] is False
    assert result["detail"]
