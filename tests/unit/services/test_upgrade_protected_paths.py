"""升级包的路径边界：受保护顶层目录**不许**写，且 `configs/` **刻意不在**名单里。

判据必须**两面都钉**：
* 只钉"拒绝" ⇒ 名单会被随手扩大，把随包目录也锁死（详见下面 `configs/` 那条）；
* 只钉"允许" ⇒ 保护形同虚设。

（此前 `PROTECTED_TOP_LEVEL` 完全**没有测试**，只有文档提到它。）
"""

from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from omnicrawler.services.updater import PROTECTED_TOP_LEVEL, UpgradeManager


def _keypair() -> tuple[ed25519.Ed25519PrivateKey, bytes]:
    private = ed25519.Ed25519PrivateKey.generate()
    return private, private.public_key().public_bytes_raw()


def _package(
    path: Path, private: ed25519.Ed25519PrivateKey, *, payloads: dict[str, bytes]
) -> Path:
    manifest = {
        "version": "9.9.9",
        "files": {name: hashlib.sha256(body).hexdigest() for name, body in payloads.items()},
    }
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    signed = {**manifest, "signature": base64.b64encode(private.sign(canonical)).decode()}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("upgrade.json", json.dumps(signed, ensure_ascii=False))
        for name, body in payloads.items():
            archive.writestr(name, body)
    return path


@pytest.mark.parametrize("top", sorted(PROTECTED_TOP_LEVEL))
def test_upgrade_package_cannot_write_protected_top_level(tmp_path: Path, top: str) -> None:
    """名单里的每个顶层名都必须被拒 —— 逐个参数化，新增名单项会自动被覆盖。"""
    private, public = _keypair()
    app = tmp_path / "app"
    app.mkdir()
    package = _package(tmp_path / f"{top.replace('.', '_')}.zip", private, payloads={f"{top}/evil.txt": b"x"})

    manager = UpgradeManager(app, trusted_public_key=public)
    with pytest.raises(ValueError, match="受保护或不安全路径"):
        manager.stage(package)
    assert not (app / top).exists(), f"{top}/evil.txt 不应被写出"


def test_protected_top_level_covers_market_installed_plugins() -> None:
    """`plugins_installed/`（市场安装的插件）不该被应用升级包覆盖。

    构建脚本 `build_*.ps1/sh` **从不产出**该目录 ⇒ 它是纯运行期用户数据，
    合法升级包没有任何理由写它。
    """
    assert "plugins_installed" in PROTECTED_TOP_LEVEL


def test_upgrade_package_may_update_shipped_configs(tmp_path: Path) -> None:
    """★ 反向：`configs/` **必须可被升级包更新**，不能为了"更安全"整目录锁死。

    它是**随包目录**（构建脚本把仓库里的 `configs/` 原样复制进发行包，其中
    `configs/plugin_trust.pub.pem` 是内置信任根）⇒ 整目录保护会让
    "升级时轮换信任根"永远做不到。所以对它的正确约束是
    "只允许随包文件被更新"，而不是"禁止触碰"。
    """
    assert "configs" not in PROTECTED_TOP_LEVEL

    private, public = _keypair()
    app = tmp_path / "app"
    (app / "configs").mkdir(parents=True)
    (app / "configs" / "plugin_trust.pub.pem").write_bytes(b"old-trust-root")
    package = _package(
        tmp_path / "cfg.zip", private, payloads={"configs/plugin_trust.pub.pem": b"new-trust-root"}
    )

    manager = UpgradeManager(app, trusted_public_key=public)
    staged = manager.stage(package)
    manager.apply(Path(str(staged["stage"])))
    assert (app / "configs" / "plugin_trust.pub.pem").read_bytes() == b"new-trust-root"
