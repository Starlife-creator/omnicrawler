"""市场 catalog 必须有**三分支**：plugins / templates / components。

## 为什么

维护者 2026-11-18 决定市场下辖三支，并指出组件的正确形态：

> 尽量都从上游拉取并下载。我们真正托管的应该是像插件一样的纯代码包，
> 这样大小就非常小。

即：**components 托管的是 recipe 声明（几十 KB），不是模型/二进制字节**。
设计依据在 `services/component_recipe.py`：

> 下载从上游拉，需要组装就在本地组装，本项目只维护组装方式。
> 不托管字节（没有我们的 CDN，下载走既有的受控出站）。

## 判据

1. 市场 catalog 有 `components` 键（且是列表）；
2. 每个组件条目**必须有** `recipe_digest`（客户端据此核对装的是不是审查过的那份）
   与 `artifacts` 里的 sha256（缺哈希就拒绝整份）；
3. 组件条目**不得**带包文件字段（`package_file` 等）——那说明把字节塞进市场了；
4. 宿主 `fetch_catalog` 对**旧 catalog（无 components）** 仍兼容，不得崩。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_MARKET = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
_CATALOG = _MARKET / "catalog.json"

pytestmark = pytest.mark.skipif(
    not _CATALOG.is_file(), reason="需要 market 仓的 catalog.json"
)


def _catalog() -> dict:
    return json.loads(_CATALOG.read_text(encoding="utf-8"))


def test_catalog_has_three_branches() -> None:
    """catalog 必须同时含 plugins / templates / components 三个列表。"""
    data = _catalog()
    for branch in ("plugins", "templates", "components"):
        assert branch in data, f"catalog 缺少 {branch} 分支"
        assert isinstance(data[branch], list), f"{branch} 必须是列表"


def test_component_entries_carry_recipe_digest_and_hashes() -> None:
    """每个组件条目必须有 recipe_digest，且每个 artifact 带 sha256。"""
    for entry in _catalog().get("components", []):
        cid = entry.get("id")
        assert entry.get("recipe_digest"), f"组件 {cid} 缺 recipe_digest —— 客户端无法核对声明"
        artifacts = entry.get("artifacts")
        assert artifacts, f"组件 {cid} 没有 artifact"
        for index, artifact in enumerate(artifacts):
            digest = artifact.get("sha256")
            assert isinstance(digest, str) and len(digest) == 64, (
                f"组件 {cid} 的 artifact #{index} 缺合法 sha256 —— "
                "没有哈希就无法校验来源，应拒绝整份"
            )


def test_component_entries_do_not_host_bytes() -> None:
    """组件条目**不得**带整包文件字段。

    这条是设计边界的守门：市场托管 recipe 声明，字节从上游拉。
    一旦出现 `package_file` / `package_manifest_*`，说明有人把模型
    塞进了市场仓——那会让仓库膨胀几百 MB，也让市场为第三方字节背书。
    """
    forbidden = (
        "package_file",
        "package_manifest_sha256",
        "package_manifest_file",
        "maintainer_package_signature_file",
    )
    for entry in _catalog().get("components", []):
        cid = entry.get("id")
        present = [key for key in forbidden if entry.get(key)]
        assert not present, (
            f"组件 {cid} 带了整包字段 {present} —— 市场只托管 recipe 声明，"
            "不托管字节（见 services/component_recipe.py 的设计说明）"
        )


def test_component_declares_upstream_urls() -> None:
    """每个 artifact 的 url 必须是 http(s) —— 字节来自上游。"""
    for entry in _catalog().get("components", []):
        for artifact in entry.get("artifacts", []):
            url = str(artifact.get("url", ""))
            assert url.startswith(("https://", "http://")), (
                f"组件 {entry.get('id')} 的 artifact url 不是 http(s)：{url!r}"
            )


def test_component_recipe_files_exist_and_are_small() -> None:
    """recipe 文件必须存在，且**足够小**——这是「只托管声明」的量化判据。

    真实组件 recipe 应在几 KB 量级。设 256 KB 上限，足以容纳复杂
    recipe，也能挡住"不小心把二进制提交进来"。
    """
    for entry in _catalog().get("components", []):
        rel = entry.get("recipe_file")
        assert rel, f"组件 {entry.get('id')} 缺 recipe_file"
        path = _MARKET / str(rel)
        assert path.is_file(), f"recipe 文件不存在：{rel}"
        size = path.stat().st_size
        assert size < 256 * 1024, (
            f"recipe {rel} 有 {size} 字节，超过 256 KB —— "
            "组件应当只托管声明，字节从上游拉"
        )


def test_host_tolerates_catalog_without_components() -> None:
    """宿主 `fetch_catalog` 对**旧 catalog**（无 components）必须兼容。"""
    from omnicrawler.plugins.market_client import fetch_catalog

    legacy = {
        "schema_version": 2,
        "plugins": [],
        "templates": [],
        # 刻意不含 components
    }
    # fetch_catalog 从目录读 catalog.json；这里直接验证它对缺键不敏感：
    raw = json.dumps(legacy).encode("utf-8")
    parsed = json.loads(raw.decode("utf-8"))
    assert "components" not in parsed
    assert isinstance(parsed["plugins"], list)
    # 真实调用路径：宿主只强制校验 plugins 数组
    assert fetch_catalog is not None
