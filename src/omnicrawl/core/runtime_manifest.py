"""运行时完整性清单（自校验用途）。

⚠ B05-022：本清单是**自签名、非信任边界**——清单与文件在本地同一目录生成，
任何人都可同时替换清单与文件。它只用于：
- release CI 对构建产物的完整性自检（防构建过程损坏/遗漏）；
- 本地 `runtime-verify` 检测意外文件变更。

**不得**用于离线校验发布包的签名、防篡改或供应链信任判定——那些场景必须
使用签名 + 透明日志（见 plugins 信任链 / sign_plugin.py）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from .utils import atomic_write, utcnow

RUNTIME_MANIFEST = "RUNTIME-MANIFEST.json"

# 运行时可变目录：打包后的 exe 每次启动都会写入/追加日志，若纳入完整性
# 清单，runtime-verify 自身启动写日志就会让哈希漂移而误报 corrupt（release
# CI 实测）。创建与校验两侧都必须排除，保持一致。
# 排除规则按**任意路径层级**名为 logs 的目录匹配——Windows/Linux 日志在顶层
# logs/，macOS .app 日志在 Contents/MacOS/logs/，前缀匹配覆盖不到后者。
_EXCLUDED_RELATIVE_DIRS = ("logs",)


def _is_excluded_relative(relative: str) -> bool:
    return any(part in _EXCLUDED_RELATIVE_DIRS for part in PurePosixPath(relative).parts)


def create_runtime_manifest(root: Path, *, include: Iterable[Path] | None = None) -> dict[str, Any]:
    """创建运行时清单（自签名，非信任边界——见模块 docstring）。"""
    root = root.resolve()
    paths = include if include is not None else (path for path in root.rglob("*") if path.is_file())
    files: dict[str, dict[str, Any]] = {}
    for path in sorted((Path(item) for item in paths), key=str):
        if path.name == RUNTIME_MANIFEST or root not in path.parents:
            continue
        # 用不 resolve 的相对路径记录（与 verify_runtime_manifest 的磁盘扫描对称）。
        # macOS .app 内 Frameworks/Python.framework 等为 symlink：若此处 resolve，
        # 记录的是解析后路径（甚至落在 root 外被跳过），而 verify 侧按 symlink
        # 相对路径扫描 → 大量 unknown（macOS release CI 实测）。Linux 已 cp -rL
        # 解引用、Windows 无 symlink，不受影响。
        relative = path.relative_to(root).as_posix()
        if _is_excluded_relative(relative):
            continue
        files[relative] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    manifest = {"format": 1, "created_at": utcnow(), "files": files}
    atomic_write(root / RUNTIME_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    return manifest


def verify_runtime_manifest(root: Path) -> dict[str, Any]:
    """校验运行时清单（S2.3.3 未知文件检测 + S4.5 P3#149 format 校验）。"""
    root = root.resolve()
    path = root / RUNTIME_MANIFEST
    if not path.is_file():
        return {"ok": False, "status": "missing_manifest", "missing": [], "corrupt": [], "unknown": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return {"ok": False, "status": "invalid_manifest", "missing": [], "corrupt": [], "unknown": []}
    # S4.5 P3#149：manifest format 不匹配即视为无效
    manifest_format = value.get("format")
    if manifest_format != 1:
        return {
            "ok": False, "status": "unsupported_format",
            "missing": [], "corrupt": [], "unknown": [], "format": manifest_format,
        }
    files = value.get("files", {}) if isinstance(value, dict) else {}
    missing: list[str] = []
    corrupt: list[str] = []
    for name, expected in files.items():
        manifest_relative = PurePosixPath(str(name))
        if manifest_relative.is_absolute() or ".." in manifest_relative.parts:
            corrupt.append(str(name))
            continue
        target = root.joinpath(*manifest_relative.parts)
        if not target.is_file():
            missing.append(str(name))
        elif target.stat().st_size != int(expected["bytes"]) or _sha256(target) != expected["sha256"]:
            corrupt.append(str(name))
    # S3.2.3：清单比对——磁盘上存在但清单未声明的文件（新增未知文件）
    declared = set(files)
    unknown: list[str] = []
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.name == RUNTIME_MANIFEST:
            continue
        relative = candidate.relative_to(root).as_posix()
        if _is_excluded_relative(relative):
            continue
        if relative not in declared:
            unknown.append(relative)
    unknown.sort()
    return {
        "ok": not missing and not corrupt and not unknown,
        "status": "valid" if not missing and not corrupt and not unknown else "invalid",
        "missing": missing,
        "corrupt": corrupt,
        "unknown": unknown,
        "checked": len(files),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
