"""Explicit offline OCR executable protocol; never import wheels into the host."""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.runtime_paths import portable_data_root
from .component_compatibility import check_registry
from .component_manager import ComponentManager, _safe_component_path, _verify_ed25519
from .component_registry import (
    contained_path,
    read_document,
    recover_document,
    registry_lock,
    safe_identifier,
)
from .update_feed import decode_public_key


@dataclass(frozen=True, slots=True)
class OCRRuntime:
    directory: Path
    executable: Path
    engine: str
    version: str


def default_component_manager() -> ComponentManager:
    root = portable_data_root() / ".omnicrawler/components"
    trust = root.parent / "component_trust.pub.pem"
    if not trust.is_file():
        raise ValueError("缺少组件受信公钥，请先配置 component_trust.pub.pem 并导入受信 OCR 组件")
    return ComponentManager(root, trusted_public_key=decode_public_key(trust.read_text(encoding="utf-8")))


@contextmanager
def leased_ocr_runtime(name: str, *, engine: str, manager: ComponentManager | None = None) -> Iterator[OCRRuntime]:
    safe_identifier(name)
    manager = manager or default_component_manager()
    if manager.trusted_public_key is None:
        raise ValueError("OCR运行入口需要受信组件签名")
    with registry_lock(manager.root):
        recover_document(manager.root)
        installed = manager._installed()
        entry = installed.get(name)
        if entry is None:
            raise ValueError(f"OCR组件未安装: {name}；请离线导入匹配组件")
        check_registry(installed)
        manifest_path = contained_path(manager.root, f".manifests/{name}/{entry['version']}.json")
        signed: dict[str, Any] = read_document(manifest_path)
        signature = str(signed.pop("signature", ""))
        canonical = json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        _verify_ed25519(manager.trusted_public_key, canonical, signature)
        for key in ("name", "version", "files", "runtime"):
            if signed.get(key) != entry.get(key):
                raise ValueError("组件运行清单与受信版本不一致")
        runtime = signed.get("runtime", {})
        if runtime.get("protocol") != "ocr-file-v1" or runtime.get("engine") != engine:
            raise ValueError("OCR组件运行协议或引擎不兼容")
        if not runtime.get("model_license"):
            raise ValueError("OCR组件缺少模型许可")
        # Check compatibility against the signed metadata as well, so local
        # registry edits cannot erase platform or dependency constraints.
        check_registry({**installed, name: signed})
        manager._verify_files(entry)
        relative = str(runtime.get("entrypoint", ""))
        _safe_component_path(relative)
        if relative not in entry["files"]:
            raise ValueError("OCR入口不在受信文件清单")
        directory = manager._entry_path(entry)
        paths = list(directory.rglob("*"))
        if any(path.is_symlink() or path.is_junction() for path in paths):
            raise ValueError("OCR组件目录包含链接")
        actual = {path.relative_to(directory).as_posix() for path in paths if path.is_file()}
        if actual != set(entry["files"]):
            raise ValueError("OCR组件文件集合与受信清单不一致")
        executable = contained_path(manager.root, str(directory / relative))
        if executable.suffix.casefold() in {".py", ".bat", ".cmd", ".ps1"}:
            raise ValueError("OCR组件需要独立可执行运行时")
        # Holding the shared transaction lease prevents activation/uninstall
        # while the caller is executing. No new daemon or lock hierarchy.
        yield OCRRuntime(directory, executable, engine, str(entry["version"]))
