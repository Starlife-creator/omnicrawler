"""fetching 单测的 SecretsStore 隔离（U5 起 session_crypto 会经默认 SecretsStore 取密钥）。

★ 若不隔离，``SecretsStore()`` 的缺省路径会落到**真实**的 ``~/.omnicrawler/secrets.bin``
—— 测试绝不摸真密钥库（与 ``tests/gui/test_identity_dialog.py`` 同一隔离手法）。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_secret_store(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")
    # keyring 停用后 SecretsStore 走口令派生兜底，需给一个测试口令（固定值即可）。
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "test-only-master-password")
