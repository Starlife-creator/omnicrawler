import sys
from types import SimpleNamespace

from omnicrawler.core import credentials


def test_os_credential_failure_does_not_block_encrypted_store(monkeypatch):
    def unavailable(*args):
        raise OSError("OS backend denied")
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_password=unavailable))
    monkeypatch.setattr(credentials, "SecretsStore", lambda: SimpleNamespace(get=lambda name: "test-fallback"))
    assert credentials.get_secret("test.nonexistent.reference") == "test-fallback"
