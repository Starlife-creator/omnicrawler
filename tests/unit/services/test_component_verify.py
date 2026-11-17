"""组件 verify：校验已安装组件的文件完整性（只读）。

## 为什么需要

三分支成熟度对齐的一部分：插件与模板都有「校验」动作，组件此前没有。
安装时校验过一次哈希，但**运行期文件可能被改动或损坏**——这正是
「校验」要回答的问题。

## 判据

* 全部文件哈希相符 ⇒ `ok=True`；
* 文件被改动 ⇒ `ok=False` 且 `problems` 指出 `digest_mismatch`；
* 文件被删除 ⇒ `missing`；
* 清单缺失 ⇒ `missing_manifest`（**不能假装通过**）；
* 越界路径 ⇒ `unsafe_path`；
* **纯只读**：verify 前后目录内容与 mtime 不变。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from omnicrawler.services.component_manager import ComponentManager


def _build_component_package(root: Path, *, payload: bytes = b"payload-v1") -> Path:
    """构造一个最小组件 zip（component.json + 一个文件）。"""
    files = {"payload.bin": hashlib.sha256(payload).hexdigest()}
    manifest = {
        "name": "demo-pack",
        "version": "1.0.0",
        "files": files,
        "dependencies": [],
        "runtime": {},
    }
    package = root / "demo-pack.ocp"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("component.json", json.dumps(manifest))
        archive.writestr("payload.bin", payload)
    return package


@pytest.fixture
def manager(tmp_path: Path) -> ComponentManager:
    root = tmp_path / "components-root"
    root.mkdir()
    return ComponentManager(root)


def _install(manager: ComponentManager, tmp_path: Path) -> str:
    package = _build_component_package(tmp_path)
    manager.import_offline(package, allow_unsigned=True)
    return "demo-pack"


def test_verify_passes_for_intact_component(manager: ComponentManager, tmp_path: Path) -> None:
    name = _install(manager, tmp_path)
    report = manager.verify(name)
    assert report["ok"] is True, report
    assert report["checked"] >= 1
    assert report["problems"] == []


def test_verify_detects_tampered_file(manager: ComponentManager, tmp_path: Path) -> None:
    name = _install(manager, tmp_path)
    entry = next(item for item in manager.list() if item["name"] == name)
    target = Path(entry["path"]) / "payload.bin"
    target.write_bytes(b"tampered")

    report = manager.verify(name)
    assert report["ok"] is False
    reasons = {p["reason"] for p in report["problems"]}
    assert "digest_mismatch" in reasons, report


def test_verify_detects_missing_file(manager: ComponentManager, tmp_path: Path) -> None:
    name = _install(manager, tmp_path)
    entry = next(item for item in manager.list() if item["name"] == name)
    (Path(entry["path"]) / "payload.bin").unlink()

    report = manager.verify(name)
    assert report["ok"] is False
    reasons = {p["reason"] for p in report["problems"]}
    assert "missing" in reasons, report


def test_verify_reports_missing_manifest_instead_of_passing(
    manager: ComponentManager, tmp_path: Path
) -> None:
    """清单缺失时必须判失败，不能因为没有清单就默认通过。"""
    name = _install(manager, tmp_path)
    for manifest in manager.root.glob(f".manifests/{name}/*.json"):
        manifest.unlink()

    report = manager.verify(name)
    assert report["ok"] is False
    assert report["problems"][0]["reason"] == "missing_manifest"


def test_verify_is_read_only(manager: ComponentManager, tmp_path: Path) -> None:
    """verify 不得修改任何文件（含 mtime）。"""
    name = _install(manager, tmp_path)
    entry = next(item for item in manager.list() if item["name"] == name)
    target = Path(entry["path"]) / "payload.bin"
    before = (target.stat().st_mtime_ns, target.read_bytes())

    manager.verify(name)

    after = (target.stat().st_mtime_ns, target.read_bytes())
    assert before == after, "verify 改变了已安装文件"


def test_verify_unknown_component_raises(manager: ComponentManager) -> None:
    with pytest.raises(ValueError, match="未安装"):
        manager.verify("no-such-component")
