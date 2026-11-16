"""组件信任根解析：两级查找 + fail-closed（条目 3.2 / 5 的前置）。

**修的是什么**

组件验签此前只认 ``portable_data_root()/.omnicrawler/component_trust.pub.pem``
——那是**运行期数据目录**，不在随包发布的内容里。后果：用户必须自己把公钥
放进去，否则组件功能默认不可用。这与「组件开箱可用」直接冲突。

另外两把钥匙（``configs/update_trust.pub.pem``、``configs/plugin_trust.pub.pem``）
都是随包发布的，查找方式一致。本模块让组件跟上同一套。

**判据四条**

1. **显式优先**：环境变量指向的文件存在时用它；
2. **随包兜底**：``<app>/configs/component_trust.pub.pem`` 存在时用它；
3. **历史位置兼容**：``.omnicrawler/component_trust.pub.pem`` 仍认（不破坏已部署用户）；
4. **都没有 ⇒ None（fail-closed）**：绝不返回一把"默认"钥匙，
   也绝不因为找不到就跳过验签。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """把三个查找位置都指向空目录，确保测试互不污染。"""
    from omnicrawler.services import component_trust as ct

    app = tmp_path / "app"
    data = tmp_path / "data"
    (app / "configs").mkdir(parents=True)
    (data / ".omnicrawler").mkdir(parents=True)

    monkeypatch.delenv("OMNICRAWL_COMPONENT_TRUST", raising=False)
    monkeypatch.setattr(ct, "application_dir", lambda: app)
    monkeypatch.setattr(ct, "portable_data_root", lambda: data)
    return ct, app, data


def test_missing_everywhere_returns_none(clean_env) -> None:
    """三处都没有 ⇒ None（fail-closed），绝不返回默认钥匙。"""
    ct, _app, _data = clean_env
    assert ct.resolve_trust_path() is None
    assert ct.load_trusted_key() is None


def test_bundled_config_wins_over_legacy(clean_env) -> None:
    """随包 ``configs/`` 优先于历史数据目录位置。"""
    ct, app, data = clean_env
    bundled = app / "configs" / "component_trust.pub.pem"
    legacy = data / ".omnicrawler" / "component_trust.pub.pem"
    bundled.write_text("BUNDLED", encoding="utf-8")
    legacy.write_text("LEGACY", encoding="utf-8")

    resolved = ct.resolve_trust_path()
    assert resolved == bundled, (
        "随包信任根应优先于历史位置——否则用户升级后仍会用旧的自备钥匙"
    )


def test_legacy_location_still_works(clean_env) -> None:
    """只有历史位置有文件时仍认它（不破坏已部署用户）。"""
    ct, _app, data = clean_env
    legacy = data / ".omnicrawler" / "component_trust.pub.pem"
    legacy.write_text("LEGACY", encoding="utf-8")
    assert ct.resolve_trust_path() == legacy


def test_explicit_env_overrides_both(clean_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """显式配置优先级最高（部署级覆盖）。"""
    ct, app, data = clean_env
    (app / "configs" / "component_trust.pub.pem").write_text("BUNDLED", encoding="utf-8")
    (data / ".omnicrawler" / "component_trust.pub.pem").write_text("LEGACY", encoding="utf-8")
    explicit = tmp_path / "explicit.pem"
    explicit.write_text("EXPLICIT", encoding="utf-8")

    monkeypatch.setenv("OMNICRAWL_COMPONENT_TRUST", str(explicit))
    assert ct.resolve_trust_path() == explicit


def test_explicit_env_pointing_nowhere_is_not_silently_ignored(
    clean_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式配置指向不存在的文件 ⇒ None，**不**回落到随包钥匙。

    理由：用户显式指定了信任根，说明他有意换根；静默回落到内置根会让他
    以为自己的配置生效了，实际仍在验内置根——这是最危险的一类"看起来对了"。
    """
    ct, app, _data = clean_env
    (app / "configs" / "component_trust.pub.pem").write_text("BUNDLED", encoding="utf-8")
    monkeypatch.setenv("OMNICRAWL_COMPONENT_TRUST", str(tmp_path / "does-not-exist.pem"))

    assert ct.resolve_trust_path() is None, (
        "显式配置失效却回落到内置根：用户以为换根了，其实没有"
    )


def test_real_keypair_roundtrip(clean_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """用真实 ed25519 密钥对验证：能读出 32 字节公钥。"""
    from omnicrawler.plugins.signing import generate_keypair

    ct, _app, _data = clean_env
    private_pem, public_pem = generate_keypair()
    path = tmp_path / "component_trust.pub.pem"
    path.write_bytes(public_pem)
    monkeypatch.setenv("OMNICRAWL_COMPONENT_TRUST", str(path))

    key = ct.load_trusted_key()
    assert key is not None and len(key) == 32
    assert private_pem  # 私钥存在（但本测试不落盘）


def test_missing_message_is_actionable(clean_env) -> None:
    """缺信任根的提示必须告诉用户**放哪里**，否则他只能猜。"""
    ct, _app, _data = clean_env
    message = ct.describe_missing()
    assert "component_trust.pub.pem" in message
    assert "configs" in message, "没提随包位置"
    assert "OMNICRAWL_COMPONENT_TRUST" in message, "没提环境变量覆盖方式"


def test_component_runtime_refuses_without_trust(clean_env) -> None:
    """没有信任根时，运行入口必须**拒绝**而不是放行。"""
    from omnicrawler.services import component_runtime

    with pytest.raises(ValueError) as excinfo:
        component_runtime.default_component_manager()
    assert "受信公钥" in str(excinfo.value) or "trust" in str(excinfo.value).lower()


def test_commands_manager_tolerates_missing_trust(clean_env, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 侧构造管理器**不抛错**（允许 `components list` 这类只读操作）。

    区别很重要：只读列目录不需要信任根；只有真正验签/安装时才必须拒绝。

    组件根目录改指临时目录：默认位置在用户数据目录下，
    测试环境可能无权创建（那是环境限制，不该让本判据失败）。
    """
    from omnicrawler.commands import components as cmd

    monkeypatch.setattr(cmd, "portable_data_root", lambda: tmp_path)
    manager = cmd.manager()
    assert manager.trusted_public_key is None
