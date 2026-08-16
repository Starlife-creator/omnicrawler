from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.config import AppConfig, load_config
from ..core.utils import atomic_write, utcnow
from ..quality.artifact_integrity import verify_artifacts
from ..security.security_audit import scan_config_text
from ..state import StateStore
from .research_package import create_research_package

WORKSPACE_FORMAT = 1
WORKSPACE_DIRECTORIES = (
    "config_versions", "raw", "attachments", "rules", "review", "logs", "output",
    "snapshots", "temp", "components",
)


def _reject_plaintext_config(config_path: Path) -> None:
    """导出前明文凭据扫描（S2.2.2）：命中即拒绝，避免凭据流出工作区包。"""
    report = scan_config_text(config_path.read_text(encoding="utf-8", errors="replace"))
    if report["ok"]:
        return
    lines = "、".join(str(item["line"]) for item in report["findings"])
    raise ValueError(
        f"配置文件包含 {len(report['findings'])} 处明文凭据（第 {lines} 行），"
        "已拒绝导出；请改用 secret:// 引用或环境变量。"
    )


class WorkspaceManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.root = config.workspace.resolve()
        self.manifest_path = self.root / "workspace.json"

    def initialize(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in WORKSPACE_DIRECTORIES:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        manifest = self.manifest()
        manifest.update({
            "format": WORKSPACE_FORMAT, "project": self.config.project_name,
            "config_path": str(self.config.path), "updated_at": utcnow(),
            "directories": list(WORKSPACE_DIRECTORIES),
        })
        atomic_write(self.manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        return manifest

    def manifest(self) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"created_at": utcnow()}
        return value if isinstance(value, dict) else {"created_at": utcnow()}

    def package(self, target: Path, *, kind: str = "full") -> dict[str, Any]:
        _reject_plaintext_config(self.config.path)
        if kind == "full":
            return self._full_package(target)
        if kind == "support":
            return create_research_package(self.config, target, include_raw=False, include_artifacts=False)
        if kind != "config":
            raise ValueError("工作区包类型必须是full、config或support")
        payload = self.config.path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("project/config.yaml", payload)
            archive.writestr("omnicrawler-package.json", json.dumps({
                "format": 1, "kind": "config-only", "files": {"project/config.yaml": digest}
            }, ensure_ascii=False, indent=2))
        return {"created": str(target), "kind": kind, "files": 1, "sha256": _sha256(target)}

    def _full_package(self, target: Path) -> dict[str, Any]:
        self.initialize()
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        hashes: dict[str, str] = {}
        file_count = 0
        with tempfile.TemporaryDirectory(prefix="omnicrawler-full-package-") as temporary:
            state_source = self.root / "state.sqlite3"
            state_snapshot: Path | None = None
            if state_source.is_file():
                state_snapshot = Path(temporary) / "state.sqlite3"
                source = sqlite3.connect(state_source)
                destination = sqlite3.connect(state_snapshot)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
                    source.close()
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:

                def _add_bytes(name: str, payload: bytes) -> None:
                    nonlocal file_count
                    hashes[name] = hashlib.sha256(payload).hexdigest()
                    archive.writestr(name, payload)
                    file_count += 1

                def _add_file(name: str, path: Path) -> None:
                    # S2.5.17：流式写出，多 GB 文件不整读内存
                    nonlocal file_count
                    digest = hashlib.sha256()
                    with archive.open(name, "w", force_zip64=True) as member, path.open("rb") as source:
                        while chunk := source.read(1 << 20):
                            digest.update(chunk)
                            member.write(chunk)
                    hashes[name] = digest.hexdigest()
                    file_count += 1

                _add_bytes("project/config.yaml", self.config.path.read_bytes())
                _add_bytes("project/workspace.json", self.manifest_path.read_bytes())
                for path in sorted(item for item in self.root.rglob("*") if item.is_file()):
                    if path.resolve() == target or path == state_source:
                        continue
                    relative = path.relative_to(self.root).as_posix()
                    if relative.split("/", 1)[0] == "output":
                        continue  # S2.5.17：排除旧导出，避免重复与体积
                    _add_file(f"project/workspace/{relative}", path)
                if state_snapshot is not None:
                    _add_file("project/workspace/state.sqlite3", state_snapshot)
                manifest = {
                    "format": 1, "kind": "full-workspace", "created_at": utcnow(), "files": hashes,
                }
                archive.writestr(
                    "omnicrawler-package.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
        return {"created": str(target), "kind": "full", "files": file_count, "sha256": _sha256(target)}

    def health(self) -> dict[str, Any]:
        self.initialize()
        database = self.root / "state.sqlite3"
        db_status = "not_started"
        artifact_report: dict[str, Any] = {"ok": True, "total": 0}
        if database.is_file():
            connection = sqlite3.connect(database)
            try:
                db_status = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                connection.close()
            with StateStore(database) as state:
                artifact_report = verify_artifacts(state, workspace=self.root)
        usage = shutil.disk_usage(self.root)
        temporary = [str(path) for path in (self.root / "temp").rglob("*") if path.is_file()]
        missing_directories = [name for name in WORKSPACE_DIRECTORIES if not (self.root / name).is_dir()]
        return {
            "ok": db_status in {"not_started", "ok"} and artifact_report.get("ok", False) and not missing_directories,
            "database": db_status, "artifacts": artifact_report,
            "disk": {"free": usage.free, "total": usage.total},
            "temporary_files": temporary, "missing_directories": missing_directories,
            "component_manifest": str(self.root / "components" / "installed.json"),
        }

    def snapshot(self, reason: str) -> Path:
        self.initialize()
        stamp = utcnow().replace(":", "-").replace("+", "_") + f"-{time.time_ns()}"
        target = self.root / "snapshots" / f"snapshot-{stamp}.zip"
        with tempfile.TemporaryDirectory(prefix="omnicrawl-workspace-") as temporary:
            stage = Path(temporary)
            shutil.copy2(self.config.path, stage / "config.yaml")
            if (self.root / "state.sqlite3").is_file():
                source = sqlite3.connect(self.root / "state.sqlite3")
                destination = sqlite3.connect(stage / "state.sqlite3")
                try:
                    source.backup(destination)
                finally:
                    destination.close()
                    source.close()
            (stage / "snapshot.json").write_text(json.dumps({
                "reason": reason, "created_at": utcnow(), "app_compatibility": "1.3+",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in stage.iterdir():
                    archive.write(path, path.name)
        return target

    def transactional_upgrade(self, operation: Callable[[], Any]) -> dict[str, Any]:
        snapshot = self.snapshot("before_upgrade")
        try:
            result = operation()
        except Exception:
            self.rollback(snapshot)
            raise
        return {"snapshot": str(snapshot), "result": result}

    def rollback(self, snapshot: Path) -> dict[str, Any]:
        snapshot = snapshot.resolve()
        if snapshot.parent != (self.root / "snapshots").resolve() or not snapshot.is_file():
            raise ValueError("只能回滚当前工作区snapshots目录中的有效快照")
        with zipfile.ZipFile(snapshot) as archive:
            config_payload = archive.read("config.yaml")
            state_payload = archive.read("state.sqlite3") if "state.sqlite3" in archive.namelist() else None
        preserved = self.snapshot("before_rollback")
        atomic_write(self.config.path, config_payload)
        load_config(self.config.path)
        if state_payload is not None:
            atomic_write(self.root / "state.sqlite3", state_payload)
        return {"restored": str(snapshot), "rollback_snapshot": str(preserved)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
