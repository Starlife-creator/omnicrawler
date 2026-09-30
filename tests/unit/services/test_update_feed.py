"""应用自更新：更新源文档的验签、字段校验与版本判定（纯逻辑，全程离线）。

这里钉的是 issue #88 的两条红线，且都做成**能失败**的判据：

* **没有信任根就没有更新**：未配置 ⇒ 报错（禁用），**不是**"降级为不校验"；
* **文档被改动即拒绝**：签名覆盖除 `signature` 外的全部字段，改任何一处都必须转红。
"""

from __future__ import annotations

import base64
import hashlib
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
        ({}, "没有 assets"),
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


# ── ⑥ 信任根来源：PEM 文本 / PEM 文件路径 / hex / base64 ────────────────


def test_decode_public_key_accepts_pem_text_and_file_path(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization

    private = ed25519.Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes_raw()
    pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()

    assert uf.decode_public_key(pem) == raw  # 内联 PEM 文本
    pem_path = tmp_path / "update_trust.pub.pem"
    pem_path.write_text(pem, encoding="utf-8")
    assert uf.decode_public_key(str(pem_path)) == raw  # PEM 文件路径


def test_missing_pem_path_fails_loudly_instead_of_falling_back_to_base64(tmp_path: Path) -> None:
    """以 .pem 结尾但文件不存在 ⇒ 必须报错。

    若"路径不存在"被静默当作 base64 解析，症状会变成"验签失败/禁用"这种
    指错方向的报错（信任根明明写对了，只是文件没部署）。
    """
    missing = tmp_path / "update_trust.pub.pem"
    with pytest.raises(uf.UpdateFeedError, match="文件不存在"):
        uf.decode_public_key(str(missing))


# ── ⑦ 载荷逐文件清单与差异比对（"自动跳过相同的文件"的全部依据）────────


def _payload_doc(**payload_overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "files": {
            "app.exe": {"sha256": "a" * 64, "size": 100},
            "_internal/qt.dll": {"sha256": "b" * 64, "size": 200},
        },
        "deleted": ["_internal/removed.pyd"],
    }
    payload.update(payload_overrides)
    return _document(payload=payload)


def test_payload_files_are_parsed_with_hash_and_size() -> None:
    private, public = _keypair()
    feed = uf.verify_feed_document(_signed(_payload_doc(), private), trusted_public_key=public)
    assert set(feed.payload_files) == {"app.exe", "_internal/qt.dll"}
    assert feed.payload_files["_internal/qt.dll"].size == 200
    assert feed.payload_deleted == ("_internal/removed.pyd",)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"files": {"x": {"sha256": "zz", "size": 1}}}, "64 位十六进制"),
        ({"files": {"x": {"sha256": "a" * 64}}}, "非负整数"),
        ({"files": {"../escape.exe": {"sha256": "a" * 64, "size": 1}}}, "路径不合法"),
        (
            {
                "files": {"x": {"sha256": "a" * 64, "size": 1}},
                "deleted": ["x"],
            },
            "自相矛盾",
        ),
    ],
)
def test_bad_payload_is_rejected(payload: dict, match: str) -> None:
    private, public = _keypair()
    raw = _signed(_document(payload=payload), private)
    with pytest.raises(uf.UpdateFeedError, match=match):
        uf.verify_feed_document(raw, trusted_public_key=public)


def test_plan_payload_skips_files_whose_hash_matches() -> None:
    private, public = _keypair()
    feed = uf.verify_feed_document(_signed(_payload_doc(), private), trusted_public_key=public)

    plan = uf.plan_payload(
        feed,
        local_hashes={"app.exe": "c" * 64, "_internal/qt.dll": "b" * 64},  # qt.dll 内容相同
    )
    assert plan.to_fetch == ("app.exe",)  # 只下变了的那个
    assert plan.unchanged == 1
    assert plan.fetch_bytes == 100  # 只算要下的那个文件的体积


def test_plan_payload_counts_absent_files_as_missing() -> None:
    private, public = _keypair()
    feed = uf.verify_feed_document(_signed(_payload_doc(), private), trusted_public_key=public)
    plan = uf.plan_payload(feed, local_hashes={})
    assert plan.missing_locally == ("_internal/qt.dll", "app.exe")
    assert plan.needs_download == 2
    assert plan.fetch_bytes == 300


def test_plan_payload_reports_deleted_paths_that_still_exist_locally() -> None:
    """★ 被删除的路径**不在**目标清单里 ⇒ 不能用 `local_hashes` 判它在不在。

    （实测踩过：写成 `rel in local_hashes` 会让"需要清理的残留"恒为空。）
    """
    private, public = _keypair()
    feed = uf.verify_feed_document(_signed(_payload_doc(), private), trusted_public_key=public)

    local_hashes = {"app.exe": "a" * 64, "_internal/qt.dll": "b" * 64}
    plan = uf.plan_payload(feed, local_hashes=local_hashes)
    assert plan.present_deleted == ()  # 缺省回退：查不到（这正是那个 bug 的形态）

    plan = uf.plan_payload(
        feed,
        local_hashes=local_hashes,
        exists=lambda relative: relative in {"_internal/removed.pyd"},
    )
    assert plan.present_deleted == ("_internal/removed.pyd",)


def test_plan_payload_uses_hash_not_mere_presence() -> None:
    """★ 判据必须是"哈希相等"，不是"文件在不在"。

    若把实现弱化成"存在即跳过"（或按体积/时间戳判），本用例立刻转红 ——
    "装回缺口 ⇒ 必须红"的实证见 `.audit-tmp/reverse_assert_payload_plan.py`。
    """
    private, public = _keypair()
    feed = uf.verify_feed_document(_signed(_payload_doc(), private), trusted_public_key=public)

    # 本机**存在** app.exe，但内容与清单不同 ⇒ 必须仍要下载
    plan = uf.plan_payload(
        feed, local_hashes={"app.exe": "d" * 64, "_internal/qt.dll": "b" * 64}
    )
    assert plan.to_fetch == ("app.exe",)
    assert plan.unchanged == 1


# ── 平台能力声明：auto_apply（"能力声明必须与实现一致"）─────────────────────
# macOS 做不到自动落地（主产物 dmg、browsers/ 在 .app 之外、ad-hoc 签名会被改坏），
# 所以清单如实写 auto_apply: false，客户端只提示、引导手动安装。三处同源：
# services 里的能力表、清单字段、客户端判据。


def test_auto_apply_defaults_to_true_for_legacy_manifests() -> None:
    """老清单没有这个字段 ⇒ 保持既有行为（不因为新增字段把老发布判成"只能手动"）。"""
    private, public = _keypair()
    raw = _signed(_document(), private)
    feed = uf.verify_feed_document(raw, trusted_public_key=public)

    assert feed.auto_apply is True


def test_auto_apply_false_is_honoured() -> None:
    """清单显式声明"本平台不自动落地" ⇒ 客户端必须认。"""
    private, public = _keypair()
    document = {**_document(), "platform": "linux", "auto_apply": False}
    feed = uf.verify_feed_document(_signed(document, private), trusted_public_key=public)

    assert feed.auto_apply is False


def test_auto_apply_cannot_overclaim_a_platform() -> None:
    """★ 反向断言：清单**谎称**能自动落地时，以能力表为准（取与）。

    否则发布侧一个笔误就会让客户端去尝试一个注定失败的落地（macOS 上连包都读不了）。
    """
    private, public = _keypair()
    document = {**_document(), "platform": "macos", "auto_apply": True}
    feed = uf.verify_feed_document(_signed(document, private), trusted_public_key=public)

    assert "macos" not in uf.AUTO_APPLY_PLATFORMS
    assert feed.auto_apply is False, "能力表说做不到 ⇒ 声明 true 也不生效"


def test_auto_apply_must_be_boolean() -> None:
    """类型判据：写个字符串不算"声明"。"""
    private, public = _keypair()
    document = {**_document(), "auto_apply": "yes"}
    with pytest.raises(uf.UpdateFeedError, match="auto_apply"):
        uf.verify_feed_document(_signed(document, private), trusted_public_key=public)


# ── 用户所有区域：configs/ 不被静默覆盖（#88 验收要求 5）────────────────────
# 为什么这不是"可能发生"：更新只替换"本地哈希与目标不符"的文件，而**用户改过的那份恰好
# 一定不符** ⇒ 只要用户改过 configs/ 里的示例配置，旧行为就必然把它冲掉。
# 语义刻意选"已存在则保留、缺失则照装"（新默认项仍能到达），信任根是**例外**（否则密钥轮换发不出去）。


def test_user_owned_predicate_covers_configs_but_not_trust_roots() -> None:
    """判据本体：`configs/**` 是用户所有，**信任根除外**。"""
    assert uf.is_user_owned("configs/project.yaml") is True
    assert uf.is_user_owned("configs/sub/deep.yaml") is True
    assert uf.is_user_owned("configs/update_trust.pub.pem") is False
    assert uf.is_user_owned("configs/plugin_trust.pub.pem") is False
    assert uf.is_user_owned("runtime/foo.dll") is False
    # 归一化：反斜杠与 `./` 前缀都不能让判据"看起来像"就跑偏
    assert uf.is_user_owned("configs\\project.yaml") is True
    assert uf.is_user_owned("./configs/a.yaml") is True
    # ★ 前缀混淆：`configsevil/` 不是 `configs/`
    assert uf.is_user_owned("configsevil/x.yaml") is False


def _plan_for(files: dict[str, str], local: dict[str, str]) -> object:
    """把"内容"当输入：真算 sha256（清单要求 64 位十六进制，占位串过不了校验）。"""
    private, public = _keypair()

    def _sha(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    document = _document(
        payload={
            "files": {
                name: {"sha256": _sha(content), "size": len(content)}
                for name, content in files.items()
            }
        }
    )
    local = {name: _sha(content) for name, content in local.items()}
    feed = uf.verify_feed_document(_signed(document, private), trusted_public_key=public)
    return uf.plan_payload(feed, local_hashes=local)


def test_plan_preserves_modified_user_config_instead_of_overwriting() -> None:
    """★ 用户改过的 `configs/` 不进 `to_fetch`（不下载、不覆盖），但**如实报出**。"""
    plan = _plan_for(
        {"configs/project.yaml": "NEW", "runtime/app.bin": "NEW2"},
        {"configs/project.yaml": "USER-EDITED", "runtime/app.bin": "OLD2"},
    )

    assert "configs/project.yaml" not in plan.to_fetch, "用户改过的配置不能被覆盖"
    assert plan.preserved == ("configs/project.yaml",), "保留了哪些必须可见"
    assert plan.to_fetch == ("runtime/app.bin",), "非用户所有区域照常更新"


def test_plan_still_delivers_trust_roots() -> None:
    """★ 例外必须真的生效：信任根**照常更新** —— 否则轮换密钥后更新永久坏掉。"""
    plan = _plan_for(
        {"configs/update_trust.pub.pem": "NEW-ROOT", "configs/plugin_trust.pub.pem": "NEW-ROOT2"},
        {"configs/update_trust.pub.pem": "OLD-ROOT", "configs/plugin_trust.pub.pem": "OLD-ROOT2"},
    )

    assert set(plan.to_fetch) == {"configs/update_trust.pub.pem", "configs/plugin_trust.pub.pem"}
    assert plan.preserved == ()


def test_plan_still_installs_new_default_configs() -> None:
    """★ 保护是"不覆盖"，**不是"永不安装"**：本机没有的新默认项仍要装。"""
    plan = _plan_for({"configs/brand_new.yaml": "NEW"}, {})

    assert plan.missing_locally == ("configs/brand_new.yaml",)
    assert plan.preserved == ()
