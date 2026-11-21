from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.core.secrets_store import SecretsStore
from omnicrawler.plugins.identity import IdentityStore
from omnicrawler.plugins.package_manifest import (
    CREATOR_SIGNATURE_NAME,
    MANIFEST_NAME,
    PackageManifestError,
    sign_creator_package,
    verify_package,
)


class _FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password


def _identity(tmp_path: Path):
    secrets = SecretsStore(tmp_path / "secrets.bin", keyring_api=_FakeKeyring())
    return IdentityStore(store=secrets).create("alice", "pw")


def test_creator_package_roundtrip_covers_every_payload_file(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.py").write_text("def handle(op, payload): return {}\n", encoding="utf-8")
    (root / "plugin.yaml").write_text("id: demo\nversion: 1.0.0\n", encoding="utf-8")
    (root / "listing.md").write_text("# Demo\n", encoding="utf-8")

    signed = sign_creator_package(
        root,
        package_type="plugin",
        package_id="demo",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="plugin.py",
    )

    assert (root / MANIFEST_NAME).is_file()
    assert (root / CREATOR_SIGNATURE_NAME).is_file()
    assert (root / "creator.sig").is_file()
    assert b"\r\n" not in (root / "creator.identity").read_bytes()
    verified = verify_package(root)
    assert verified.manifest_sha256 == signed.manifest_sha256
    assert verified.creator.username == "alice"
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {
        "creator.identity",
        "listing.md",
        "plugin.py",
        "plugin.yaml",
    }


def test_package_rejects_modified_or_extra_file(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.py").write_text("def handle(op, payload): return {}\n", encoding="utf-8")
    sign_creator_package(
        root,
        package_type="plugin",
        package_id="demo",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="plugin.py",
    )
    (root / "plugin.py").write_text("raise RuntimeError('changed')\n", encoding="utf-8")
    with pytest.raises(PackageManifestError, match="哈希不一致"):
        verify_package(root)

    sign_creator_package(
        root,
        package_type="plugin",
        package_id="demo",
        version="1.0.0",
        identity=_identity(tmp_path / "other"),
        legacy_target="plugin.py",
    )
    (root / "unexpected.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(PackageManifestError, match="未声明"):
        verify_package(root)


def test_package_rejects_private_material(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "template.yaml").write_text("project: {}\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    with pytest.raises(PackageManifestError, match="私钥或凭据"):
        sign_creator_package(
            root,
            package_type="template",
            package_id="demo",
            version="1.0.0",
            identity=_identity(tmp_path),
            legacy_target="template.yaml",
        )

# ── 组件包与插件/模板**同等地位**：签名与验签都必须支持 ──────────────────
def _component_dir(tmp_path: Path, version: str = "1.0.0") -> Path:
    root = tmp_path / "probe-component"
    root.mkdir()
    (root / "recipe.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "id": "probe-component",
                "name": "probe",
                "version": version,
                "kind": "wheel-pack",
                "smoke": {"expect": ["ok"]},
                "platforms": ["windows"],
                "architectures": ["x86_64"],
                "python_abi": ["cp313"],
                "provides": ["probe.demo"],
                "artifacts": [{"url": "https://e.invalid/a.whl", "sha256": "a" * 64, "size": 1}],
                "entry": {
                    "path": "entry.json",
                    "protocol": "p",
                    "engine": "generic",
                    "runner": "worker-wheelpack",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_component_package_can_be_verified(tmp_path: Path) -> None:
    """**组件签完之后必须验得过。**

    实测缺陷（2026-11-21）：`build_manifest` 早就接受
    `("plugin", "template", "component")`，而 `verify_package` 只列了前两种
    —— 于是组件**能签但验不了**，任何验签都报
    「不支持的包类型: 'component'」。

    这个缺陷长期潜伏，因为**签名路径不经过 verify**：
    CI 里只看得到「签名成功」，看不到「验不了」。

    来源：提交 7677294e「组件市场补齐到与插件/模板同等成熟度」
    只改了签名侧，漏了验证侧。
    """
    root = _component_dir(tmp_path)
    signed = sign_creator_package(
        root,
        package_type="component",
        package_id="probe-component",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="recipe.json",
    )
    verified = verify_package(root)
    assert verified.package_type == "component"
    assert verified.manifest_sha256 == signed.manifest_sha256
    assert verified.creator.username == "alice"


def test_component_derived_files_are_not_payload(tmp_path: Path) -> None:
    """组件**派生物**不进清单：`deps.lock.json` / `files.lock.json`。

    它们由工具生成（依赖闭包快照、内置组件逐文件哈希），正确性由
    「与配方一致」决定，而**不是**由签名决定 —— 签名覆盖的是
    「作者声明了什么」，不该覆盖「工具算出了什么」。

    若把它们算进载荷，每次重算快照都要全包重签，而重算本身不需要作者参与。
    """
    root = _component_dir(tmp_path)
    (root / "deps.lock.json").write_text('{"packages": []}\n', encoding="utf-8")
    (root / "files.lock.json").write_text('{"files": {}}\n', encoding="utf-8")

    sign_creator_package(
        root,
        package_type="component",
        package_id="probe-component",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="recipe.json",
    )
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "deps.lock.json" not in manifest["files"], "派生物不该进清单"
    assert "files.lock.json" not in manifest["files"], "派生物不该进清单"
    assert "recipe.json" in manifest["files"]
    # 验签也必须通过（派生物不算「未声明文件」）
    verify_package(root)


def test_component_recipe_change_breaks_signature(tmp_path: Path) -> None:
    """改了 `recipe.json` 就必须重签 —— 否则验签必须失败。"""
    root = _component_dir(tmp_path)
    sign_creator_package(
        root,
        package_type="component",
        package_id="probe-component",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="recipe.json",
    )
    recipe = json.loads((root / "recipe.json").read_text(encoding="utf-8"))
    recipe["description"] = "改了一个字节"
    (root / "recipe.json").write_text(
        json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(PackageManifestError, match="哈希不一致"):
        verify_package(root)


def test_all_three_package_types_are_accepted_by_both_sides() -> None:
    """签名侧与验证侧支持的包类型**必须同源**。

    这条守卫直接钉住那个潜伏缺陷：两处判定各写一份、不同步，
    就会长期只签不验。
    """
    import inspect

    import omnicrawler.plugins.package_manifest as module

    build_src = inspect.getsource(module.build_manifest)
    verify_src = inspect.getsource(module.verify_package)
    for package_type in ("plugin", "template", "component"):
        assert f'"{package_type}"' in build_src, f"签名侧缺 {package_type}"
        assert f'"{package_type}"' in verify_src, (
            f"验证侧缺 {package_type} —— 会出现「能签不能验」"
        )

# ── 维护者对象级签名（第三种签名，与创作者签名分工明确）────────────────
def test_maintainer_signature_is_required_when_asked(tmp_path: Path) -> None:
    """`require_maintainer=True` 时，缺维护者签名必须判红。

    两种签名的语义不同：
    * 创作者签名 = 「这是我写的」
    * 维护者签名 = 「我审过，可以进市场」

    市场发布包必须两条轨都在；只有创作者签名只能私下分发。
    """
    root = _component_dir(tmp_path)
    sign_creator_package(
        root,
        package_type="component",
        package_id="probe-component",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="recipe.json",
    )
    # 只有创作者签名 → 要求维护者时应当失败
    with pytest.raises(PackageManifestError, match="缺少维护者签名"):
        verify_package(root, maintainer_public_key=b"\x00" * 32, require_maintainer=True)


def test_maintainer_signature_verifies_with_public_key(tmp_path: Path) -> None:
    """维护者签名写进去后，用对应公钥必须验得过。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from omnicrawler.plugins.package_manifest import (
        MAINTAINER_SIGNATURE_NAME,
        canonical_manifest_bytes,
    )

    root = _component_dir(tmp_path)
    sign_creator_package(
        root,
        package_type="component",
        package_id="probe-component",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="recipe.json",
    )

    key = Ed25519PrivateKey.generate()
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    (root / MAINTAINER_SIGNATURE_NAME).write_bytes(
        key.sign(canonical_manifest_bytes(manifest))
    )

    verified = verify_package(
        root, maintainer_public_key=public_raw, require_maintainer=True
    )
    assert verified.maintainer_signed is True


def test_maintainer_signature_without_public_key_is_rejected(tmp_path: Path) -> None:
    """有维护者签名却不给公钥 ⇒ 无法验证 ⇒ 必须判红。

    这是「不能因为验不了就放过」的 fail-closed 纪律。
    """
    from omnicrawler.plugins.package_manifest import (
        MAINTAINER_SIGNATURE_NAME,
        canonical_manifest_bytes,
    )

    root = _component_dir(tmp_path)
    sign_creator_package(
        root,
        package_type="component",
        package_id="probe-component",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="recipe.json",
    )
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    (root / MAINTAINER_SIGNATURE_NAME).write_bytes(
        b"\x00" * 64  # 假签名，只为触发「有签名但没公钥」分支
    )
    assert canonical_manifest_bytes(manifest)
    with pytest.raises(PackageManifestError, match="未提供维护者公钥"):
        verify_package(root)


def test_maintainer_signature_file_is_not_payload(tmp_path: Path) -> None:
    """维护者签名文件本身不进载荷（否则自引用）。

    `MAINTAINER_SIGNATURE_NAME` 已在 `_GENERATED_NAMES` 里，
    这条守卫防止有人把它挪出去。
    """
    from omnicrawler.plugins.package_manifest import MAINTAINER_SIGNATURE_NAME

    root = _component_dir(tmp_path)
    (root / MAINTAINER_SIGNATURE_NAME).write_bytes(b"\x00" * 64)
    sign_creator_package(
        root,
        package_type="component",
        package_id="probe-component",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="recipe.json",
    )
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert MAINTAINER_SIGNATURE_NAME not in manifest["files"]
