from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PyQt6") is None,
    reason="GUI test requires PyQt6",
)


@pytest.fixture(scope="session", autouse=True)
def _qt_app():
    """整个测试会话复用同一个 QApplication（同 test_plugin_market 约定）。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _make_dialog(monkeypatch, tmp_path):
    """构造隔离的身份对话框（信任列表指向临时文件，不碰真实用户数据）。"""
    from omnicrawl.gui.views.identity_dialog import IdentityDialog
    from omnicrawl.plugins import trust as trust_module

    monkeypatch.setattr(trust_module, "DEFAULT_TRUST_LIST", tmp_path / "trusted_users.json")
    # 隔离身份存储：避免读到用户真实 ~/.omnicrawl/secrets.bin（测试期望空状态）
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    return IdentityDialog(parent=None)


def test_dialog_instantiates_and_refreshes(monkeypatch, tmp_path) -> None:
    dialog = _make_dialog(monkeypatch, tmp_path)
    assert dialog.windowTitle()
    assert dialog._id_list is not None
    assert dialog._trust_list is not None
    # 空状态：无身份、无信任记录
    assert dialog._id_list.count() == 0
    assert dialog._trust_list.count() == 0


def test_trust_add_and_revoke(monkeypatch, tmp_path) -> None:
    from omnicrawl.plugins import signing

    dialog = _make_dialog(monkeypatch, tmp_path)
    _, public_pem = signing.generate_keypair()
    dialog._trust_name.setText("alice")
    dialog._trust_pubkey.setText(public_pem.decode("ascii"))
    dialog._on_trust_add()
    assert dialog._trust_list.count() == 1
    assert "alice" in dialog._trust_list.item(0).text()

    # 幂等：重复添加不重复
    dialog._on_trust_add()
    assert dialog._trust_list.count() == 1

    dialog._trust_list.setCurrentRow(0)
    dialog._on_trust_revoke()
    assert dialog._trust_list.count() == 0
    assert not (tmp_path / "trusted_users.json").read_text(encoding="utf-8").count("alice")


def test_trust_add_requires_both_fields(monkeypatch, tmp_path) -> None:
    dialog = _make_dialog(monkeypatch, tmp_path)
    dialog._trust_name.setText("alice")
    dialog._trust_pubkey.setText("")
    dialog._on_trust_add()  # 不应抛异常，也不应写入
    assert dialog._trust_list.count() == 0
