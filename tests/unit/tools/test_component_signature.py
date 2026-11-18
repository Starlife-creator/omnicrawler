"""组件包必须带创作者签名，且签名在 catalog 里可见、可验。

## 为什么需要

市场三分支里，插件与模板都有创作者签名，**组件此前没有**（实测：`components/`
下 `creator.sig` 数量为 0，而 `templates/` 有 15 个）。这违背了三支同等成熟度的目标。

补签之后又暴露了第二个问题：**签名产出了，catalog 里却看不见**。
原因是 `build.py` 的组件分支只找 `recipe.json.sig`（单文件签名轨），
而 `creator-sign` 实际产出的是整包清单签名 `package.manifest.creator.sig`
⇒ `recipe_signature_file` 恒为 `None`。

## 判据

1. 组件目录必须有 `creator.identity` + `package.manifest.json` + 签名文件；
2. **签名必须真的能验过**（用 identity 里的公钥验 manifest 字节）；
3. **catalog 条目必须记录签名轨**，且键名与插件/模板一致
   （`package_manifest_file` / `creator_package_signature_file` / `creator_identity_file`）；
4. catalog 里记录的路径必须真实存在；
5. 反向：篡改 manifest 一个字节后，验签必须失败。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

_MARKET = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
_COMPONENTS = _MARKET / "components"
_CATALOG = _MARKET / "catalog.json"

pytestmark = pytest.mark.skipif(
    not _COMPONENTS.is_dir(), reason="需要 market 仓的 components 目录"
)


def _component_dirs() -> list[Path]:
    return sorted(p for p in _COMPONENTS.iterdir() if p.is_dir() and (p / "recipe.json").is_file())


def _catalog_components() -> list[dict]:
    if not _CATALOG.is_file():
        return []
    return json.loads(_CATALOG.read_text(encoding="utf-8")).get("components", []) or []


def test_every_component_has_signature_artifacts() -> None:
    """每个组件都必须有身份、清单与创作者签名三件套。"""
    dirs = _component_dirs()
    assert dirs, "没找到任何组件目录 —— 路径推算可能失效"

    for directory in dirs:
        for name in ("creator.identity", "package.manifest.json"):
            assert (directory / name).is_file(), (
                f"组件 {directory.name} 缺少 {name} —— 未签名就不能进市场"
            )
        signatures = [
            f for f in ("package.manifest.creator.sig", "creator.sig")
            if (directory / f).is_file()
        ]
        assert signatures, (
            f"组件 {directory.name} 没有任何创作者签名文件"
        )


def test_component_signature_verifies_cryptographically() -> None:
    """签名必须**真的能验过**，不能只是文件存在。

    用 `creator.identity` 里的公钥验 `package.manifest.json` 的字节。
    """
    Ed25519PublicKey = pytest.importorskip(
        "cryptography.hazmat.primitives.asymmetric.ed25519"
    ).Ed25519PublicKey

    dirs = _component_dirs()
    assert dirs, "没找到组件目录"

    for directory in dirs:
        identity = json.loads((directory / "creator.identity").read_text(encoding="utf-8"))
        public_key = identity.get("public_key")
        assert public_key, f"组件 {directory.name} 的 identity 缺 public_key"

        manifest_path = directory / "package.manifest.json"
        signature_path = directory / "package.manifest.creator.sig"
        if not signature_path.is_file():
            pytest.skip(f"{directory.name} 只有兼容轨签名，跳过清单验签")

        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        payload = manifest_path.read_bytes()
        raw_signature = signature_path.read_bytes()
        signature = base64.b64decode(raw_signature) if len(raw_signature) != 64 else raw_signature

        # 正例：签名有效
        key.verify(signature, payload)

        # 反向：改一个字节必须失败（断言具体异常类型，不用盲 Exception）
        from cryptography.exceptions import InvalidSignature

        with pytest.raises(InvalidSignature):
            key.verify(signature, payload + b" ")


def test_catalog_records_component_signature_track() -> None:
    """catalog 条目必须记录签名轨，键名与插件/模板一致。"""
    entries = _catalog_components()
    if not entries:
        pytest.skip("catalog 暂无组件条目")

    for entry in entries:
        cid = entry.get("id")
        assert entry.get("package_manifest_file"), (
            f"组件 {cid} 的 catalog 条目缺 package_manifest_file"
        )
        signature_keys = [
            k for k in ("creator_package_signature_file", "creator_signature_file")
            if entry.get(k)
        ]
        assert signature_keys, (
            f"组件 {cid} 的 catalog 条目没有任何签名轨字段 —— "
            "签名产出了但市场看不见，用户无法据此判断来源"
        )
        assert entry.get("creator_identity_file"), (
            f"组件 {cid} 的 catalog 条目缺 creator_identity_file"
        )
        assert entry.get("publisher") not in (None, "", "unknown"), (
            f"组件 {cid} 的发布者未解析出来（{entry.get('publisher')!r}）"
        )


def test_catalog_signature_paths_exist() -> None:
    """catalog 里记录的路径必须真实存在。"""
    entries = _catalog_components()
    if not entries:
        pytest.skip("catalog 暂无组件条目")

    for entry in entries:
        for key in (
            "recipe_file",
            "package_manifest_file",
            "creator_package_signature_file",
            "creator_identity_file",
            "creator_signature_file",
            "description_file",
        ):
            rel = entry.get(key)
            if not rel:
                continue
            assert (_MARKET / rel).is_file(), (
                f"组件 {entry.get('id')} 的 {key} 指向不存在的文件：{rel}"
            )
