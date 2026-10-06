from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from ..core.archive_security import (
    DEFAULT_ZIP_READ_LIMITS,
    copy_zip_member,
    read_zip_member,
    validate_zip_archive,
)
from ..core.config import AppConfig, load_config
from ..core.database_lease import database_maintenance, pending_restore_path
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
        self._recover_pending_rollback()
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
        if kind in {"full", "complete"}:
            return self._full_package(target, include_outputs=kind == "complete")
        if kind == "support":
            return create_research_package(self.config, target, include_raw=False, include_artifacts=False)
        if kind != "config":
            raise ValueError("工作区包类型必须是full、complete、config或support")
        payload = self.config.path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("project/config.yaml", payload)
            archive.writestr("omnicrawler-package.json", json.dumps({
                "format": 1, "kind": "config-only", "files": {"project/config.yaml": digest}
            }, ensure_ascii=False, indent=2))
        return {"created": str(target), "kind": kind, "files": 1, "sha256": _sha256(target)}

    @staticmethod
    def import_package(package: Path, destination: Path, *, expected_sha256: str = "") -> dict[str, Any]:
        from .workspace_import import import_package
        return import_package(package, destination, expected_sha256=expected_sha256)

    def _full_package(self, target: Path, *, include_outputs: bool = False) -> dict[str, Any]:
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(descriptor)
        staged = Path(temporary)
        try:
            result = self._write_full_package(staged, exclude=target, include_outputs=include_outputs)
            os.replace(staged, target)
            return {**result, "created": str(target)}
        finally:
            staged.unlink(missing_ok=True)

    def _write_full_package(self, target: Path, *, exclude: Path, include_outputs: bool = False) -> dict[str, Any]:
        self.initialize()
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        hashes: dict[str, str] = {}
        file_count = 0
        paths = sorted(item for item in self.root.rglob("*") if item.is_file())
        for path in paths:
            if path.is_symlink() or any(parent.is_symlink() or parent.is_junction() for parent in path.parents if self.root in parent.parents):
                raise ValueError("工作区打包不支持链接到其它位置的文件")
        with tempfile.TemporaryDirectory(prefix="omnicrawler-full-package-") as temporary:
            database_snapshots: dict[Path, Path] = {}
            for database in paths:
                if not database.is_file() or database.suffix not in {".sqlite3", ".sqlite", ".db"}:
                    continue
                if not include_outputs and database.relative_to(self.root).parts[0] == "output":
                    continue
                with database.open("rb") as handle:
                    if handle.read(16) != b"SQLite format 3\0":
                        continue
                copied = Path(temporary) / f"database-{len(database_snapshots)}.sqlite3"
                _backup_database(database, copied)
                database_snapshots[database] = copied
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
                for path in paths:
                    if path.resolve() in {target, exclude} or path in database_snapshots:
                        continue
                    relative = path.relative_to(self.root).as_posix()
                    if path.is_symlink() or any(parent.is_symlink() or parent.is_junction() for parent in path.parents if self.root in parent.parents):
                        raise ValueError("工作区打包不支持链接到其它位置的文件")
                    if path.name.endswith(("-wal", "-shm", ".lock", ".restore.pending.json")) or any(
                        part.endswith(".leases") for part in path.relative_to(self.root).parts
                    ):
                        continue
                    if not include_outputs and relative.split("/", 1)[0] == "output":
                        continue  # S2.5.17：排除旧导出，避免重复与体积
                    _add_file(f"project/workspace/{relative}", path)
                for database, copied in database_snapshots.items():
                    _add_file(f"project/workspace/{database.relative_to(self.root).as_posix()}", copied)
                manifest = {
                    "format": 1, "kind": "full-workspace", "created_at": utcnow(), "files": hashes,
                    "workspace_origin": str(self.root), "config_origin": str(self.config.path),
                    "project_root_origin": str(self.config.root),
                    "exports_included": include_outputs,
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
        with tempfile.TemporaryDirectory(prefix="omnicrawler-workspace-") as temporary:
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
                "files": {path.name: _sha256(path) for path in stage.iterdir() if path.is_file()},
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

    @contextmanager
    def _staged_snapshot(self, snapshot: Path) -> Iterator[tuple[bytes, Path | None]]:
        with tempfile.TemporaryDirectory(prefix="omnicrawler-restore-") as temporary:
            stage = Path(temporary)
            with zipfile.ZipFile(snapshot) as archive:
                members = validate_zip_archive(archive, required=("config.yaml",))
                config = read_zip_member(archive, members["config.yaml"],
                                         maximum_bytes=DEFAULT_ZIP_READ_LIMITS.max_manifest_bytes)
                staged_config = stage / "config.yaml"
                staged_config.write_bytes(config)
                load_config(staged_config)
                database: Path | None = None
                if "state.sqlite3" in members:
                    database = stage / "state.sqlite3"
                    copy_zip_member(archive, members["state.sqlite3"], database)
                    with closing(sqlite3.connect(database)) as connection:
                        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("快照数据库完整性校验失败")
                if "snapshot.json" in members:
                    metadata = json.loads(read_zip_member(archive, members["snapshot.json"],
                                                          maximum_bytes=DEFAULT_ZIP_READ_LIMITS.max_manifest_bytes))
                    expected = metadata.get("files") if isinstance(metadata, dict) else None
                    if expected is not None:
                        actual = {path.name: _sha256(path) for path in stage.iterdir() if path.is_file()}
                        if expected != actual:
                            raise ValueError("快照文件哈希校验失败")
            yield config, database

    def _apply_staged(self, config: bytes, database: Path | None) -> None:
        destination = self.root / "state.sqlite3"
        if database is not None:
            _backup_database(database, destination)
        else:
            for path in (destination, Path(str(destination) + "-wal"), Path(str(destination) + "-shm")):
                path.unlink(missing_ok=True)
        atomic_write(self.config.path, config)

    def _recover_pending_rollback(self) -> bool:
        pending = pending_restore_path(self.root / "state.sqlite3")
        if not pending.exists():
            return False
        with database_maintenance(self.root / "state.sqlite3"):
            record = json.loads(pending.read_text(encoding="utf-8"))
            preserved = (self.root / "snapshots" / str(record.get("preserved", ""))).resolve()
            if preserved.parent != (self.root / "snapshots").resolve() or not preserved.is_file():
                raise ValueError("回滚恢复快照缺失或路径无效")
            if _sha256(preserved) != record.get("sha256"):
                raise ValueError("回滚恢复快照已改变")
            with self._staged_snapshot(preserved) as (config, database):
                self._apply_staged(config, database)
            pending.unlink()
        return True

    def rollback(self, snapshot: Path) -> dict[str, Any]:
        self._recover_pending_rollback()
        snapshot = snapshot.resolve()
        if snapshot.parent != (self.root / "snapshots").resolve() or not snapshot.is_file():
            raise ValueError("只能回滚当前工作区snapshots目录中的有效快照")
        with self._staged_snapshot(snapshot) as (config, database):
            with database_maintenance(self.root / "state.sqlite3"):
                preserved = self.snapshot("before_rollback")
                pending = pending_restore_path(self.root / "state.sqlite3")
                atomic_write(pending, json.dumps({"preserved": preserved.name, "sha256": _sha256(preserved)},
                                                ensure_ascii=False).encode())
                try:
                    self._apply_staged(config, database)
                except BaseException:
                    # The journal remains if compensation also fails, blocking stores
                    # until health/rollback can restore the original pair on restart.
                    with self._staged_snapshot(preserved) as (old_config, old_database):
                        self._apply_staged(old_config, old_database)
                    pending.unlink()
                    raise
                pending.unlink()
        return {"restored": str(snapshot), "rollback_snapshot": str(preserved)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _backup_database(source_path: Path, destination_path: Path) -> None:
    deadline = time.monotonic() + 30

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() >= deadline:
            raise RuntimeError("数据库备份或恢复等待超时；请停止占用数据库的外部程序")

    with closing(sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as source:
        with closing(sqlite3.connect(destination_path, timeout=1)) as destination:
            source.backup(destination, pages=256, progress=progress, sleep=0.05)
            destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
