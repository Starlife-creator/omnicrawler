"""应用自更新：更新源文档的验签、字段校验与版本判定（纯逻辑，全程离线）。

这里钉的是 issue #88 的两条红线，且都做成**能失败**的判据：

* **没有信任根就没有更新**：未配置 ⇒ 报错（禁用），**不是**"降级为不校验"；
* **文档被改动即拒绝**：签名覆盖除 `signature` 外的全部字段，改任何一处都必须转红。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from omnicrawler.core.versions import is_newer, version_key
from omnicrawler.services import update_feed as uf


def _keypair() -> tuple[ed25519.Ed25519PrivateKey, bytes]:
    private = ed25519.Ed25519PrivateKey.generate()
    return private, private.public_key().public_bytes_raw()


def _document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "version": "9.9.9",
        "published_at": "2026-09-28T00:00:00Z",
        "notes": "测试版",
        "assets": {
            "linux-standard": {"name": "pkg-linux.zip", "sha256": "a" * 64, "size": 10},
            "windows-full": {"name": "pkg-win.zip", "sha256": "b" * 64, "size": 20},
        },
    }
    document.update(overrides)
    return document


def _signed(document: dict[str, object], private: ed25519.Ed25519PrivateKey) -> bytes:
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    return json.dumps({**document, "signature": signature}, ensure_ascii=False).encode()


# ── ① 信任根缺失 ⇒ 禁用，而不是"不校验" ────────────────────────────────


def test_missing_trust_root_refuses_rather_than_skipping_verification() -> None:
    private, _ = _keypair()
    raw = _signed(_document(), private)

    with pytest.raises(uf.UpdateFeedError) as excinfo:
        uf.verify_feed_document(raw, trusted_public_key=None)
    message = str(excinfo.value)
    assert "未配置更新信任根" in message
    assert "跳过验签" in message  # 明确宣告"不提供跳过开关"，而不是悄悄放行

    with pytest.raises(uf.UpdateFeedError):
        uf.verify_feed_document(raw, trusted_public_key=b"")


# ── ② 验签：改动任何字段都必须被拒 ────────────────────────────────────


def test_valid_document_verifies_and_parses() -> None:
    private, public = _keypair()
    feed = uf.verify_feed_document(_signed(_document(), private), trusted_public_key=public)

    assert feed.version == "9.9.9"
    assert set(feed.assets) == {"linux-standard", "windows-full"}
    assert feed.assets["linux-standard"].size == 10
    assert feed.assets["linux-standard"].sha256 == "a" * 64


def test_tampering_with_a_signed_field_is_rejected() -> None:
    private, public = _keypair()
    raw = _signed(_document(), private)

    # 改版本号（用同样的密钥重新序列化，但不重新签名）⇒ 必须验签失败
    tampered = json.loads(raw.decode("utf-8"))
    tampered["version"] = "99.0.0"
    with pytest.raises(uf.UpdateFeedError, match="验签失败"):
        uf.verify_feed_document(
            json.dumps(tampered, ensure_ascii=False).encode(), trusted_public_key=public
        )

    # 改资产哈希同样必须失败（这是"换包"的最短路径）
    tampered = json.loads(raw.decode("utf-8"))
    tampered["assets"]["linux-standard"]["sha256"] = "c" * 64
    with pytest.raises(uf.UpdateFeedError, match="验签失败"):
        uf.verify_feed_document(
            json.dumps(tampered, ensure_ascii=False).encode(), trusted_public_key=public
        )


def test_document_signed_by_another_key_is_rejected() -> None:
    private, _ = _keypair()
    _, other_public = _keypair()
    with pytest.raises(uf.UpdateFeedError, match="验签失败"):
        uf.verify_feed_document(_signed(_document(), private), trusted_public_key=other_public)


def test_unsigned_document_is_rejected() -> None:
    _, public = _keypair()
    raw = json.dumps(_document(), ensure_ascii=False).encode()
    with pytest.raises(uf.UpdateFeedError, match="缺少 signature"):
        uf.verify_feed_document(raw, trusted_public_key=public)


# ── ③ 字段校验：坏文档不能被"部分接受" ────────────────────────────────


@pytest.mark.parametrize(
    ("assets", "match"),
    [
        ({"linux-standard": {"name": "../escape.zip", "sha256": "a" * 64, "size": 1}}, "相对文件名"),
        ({"linux-standard": {"name": "/abs.zip", "sha256": "a" * 64, "size": 1}}, "相对文件名"),
        ({"linux-standard": {"name": "x.zip", "sha256": "zz", "size": 1}}, "64 位十六进制"),
        ({"linux-standard": {"name": "x.zip", "sha256": "a" * 64, "size": -1}}, "非负整数"),
        ({"linux-standard": {"name": "x.zip", "sha256": "a" * 64}}, "非负整数"),
        ({}, "assets 缺失或为空"),
    ],
)
def test_bad_assets_are_rejected(assets: dict, match: str) -> None:
    private, public = _keypair()
    raw = _signed(_document(assets=assets), private)
    with pytest.raises(uf.UpdateFeedError, match=match):
        uf.verify_feed_document(raw, trusted_public_key=public)


def test_uncomparable_version_is_rejected() -> None:
    private, public = _keypair()
    raw = _signed(_document(version="next"), private)
    with pytest.raises(uf.UpdateFeedError, match="不可比较"):
        uf.verify_feed_document(raw, trusted_public_key=public)


# ── ④ 版本判定与资产选择 ───────────────────────────────────────────────


def test_check_reports_update_with_asset_for_current_platform() -> None:
    private, public = _keypair()
    feed = uf.verify_feed_document(_signed(_document(), private), trusted_public_key=public)

    result = uf.check_feed(feed, current_version="0.14.0", platform="linux", edition="Standard")
    assert result.status == "update-available"
    assert result.update_available is True
    assert result.asset is not None and result.asset.name == "pkg-linux.zip"


def test_check_is_up_to_date_for_newer_or_equal_installed_version() -> None:
    private, public = _keypair()
    feed = uf.verify_feed_document(_signed(_document(), private), trusted_public_key=public)

    for current in ("9.9.9", "10.0.0"):
        result = uf.check_feed(
            feed, current_version=current, platform="linux", edition="Standard"
        )
        assert result.status == "up-to-date", current


def test_new_version_without_matching_asset_is_visible_not_silent() -> None:
    private, public = _keypair()
    feed = uf.verify_feed_document(_signed(_document(), private), trusted_public_key=public)

    result = uf.check_feed(feed, current_version="0.1.0", platform="macos", edition="Standard")
    assert result.status == "update-available"
    assert result.asset is None
    # 必须说清缺的是哪个键，否则发布者猜不出该补哪个资产
    assert "macos-standard" in result.detail


def test_asset_key_is_platform_and_edition_lowercased() -> None:
    assert uf.asset_key("Windows", "Full") == "windows-full"


def test_version_helpers_match_the_documented_semantics() -> None:
    assert version_key("0.6.4") == (0, 6, 4)
    # ★ 非数字段整段**连同位置**丢弃（不是当 0）⇒ "1.0.0-beta" 比 "1.0.0" 小。
    #   这条是实测钉出来的：最初 docstring 写成 (1,0,0)，被本用例判红后改正。
    assert version_key("1.0.0-beta") == (1, 0)
    assert version_key("1.0.0-beta") < version_key("1.0.0")
    assert is_newer("0.15.0", "0.14.0") is True
    assert is_newer("0.14.0", "0.14.0") is False
    assert is_newer("0.9.0", "0.14.0") is False  # 按数值段比，不是字符串比
    # 解析不出数字段时**不**判为更新（fail-closed）
    assert is_newer("next", "0.14.0") is False
    assert is_newer("0.15.0", "unknown") is False


# ── ⑤ 公钥解码 ─────────────────────────────────────────────────────────


def test_decode_public_key_accepts_base64_hex_and_empty() -> None:
    _, public = _keypair()
    assert uf.decode_public_key(base64.b64encode(public).decode()) == public
    assert uf.decode_public_key("hex:" + public.hex()) == public
    assert uf.decode_public_key("") is None
    assert uf.decode_public_key(None) is None


def test_decode_public_key_rejects_wrong_length() -> None:
    with pytest.raises(uf.UpdateFeedError, match="长度不对"):
        uf.decode_public_key(base64.b64encode(b"short").decode())
    with pytest.raises(uf.UpdateFeedError, match="base64"):
        uf.decode_public_key("not base64!!!")


def test_feed_path_is_local_base_plus_fixed_filename(tmp_path: Path) -> None:
    assert uf.feed_path(tmp_path) == str(tmp_path / uf.FEED_FILENAME)
    assert uf.FEED_FILENAME == "update.json"


def test_gui_version_tuple_is_the_same_single_source() -> None:
    """GUI 侧不得自带第二份比较实现（本仓已有"同一判据两处实现必漂移"的先例）。"""
    pytest.importorskip("PySide6")
    from omnicrawler.gui.views.plugin_market_logic import _version_tuple

    samples = ["0.6.4", "1.0.0-beta", "next", "0.14.0", ""]
    assert [_version_tuple(value) for value in samples] == [
        version_key(value) for value in samples
    ]
