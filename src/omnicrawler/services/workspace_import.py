"""Bounded, verified workspace relocation into a fresh directory."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from ..core.archive_security import (
    DEFAULT_ZIP_READ_LIMITS,
    copy_zip_member,
    read_zip_member,
    validate_zip_archive,
)
from ..core.utils import utcnow
from ..security.security_audit import scan_config_text
from ..state.migrations import SCHEMA_VERSION


def _relative(value: str, origin: str) -> tuple[str, ...] | None:
    path_type = PureWindowsPath if re.match(r"^[A-Za-z]:[\\/]", origin) or "\\" in origin else PurePosixPath
    try:
        result = path_type(value).relative_to(path_type(origin))
    except ValueError:
        return None
    return result.parts if result.parts and ".." not in result.parts else None


def _rebind_database(database: Path, stage_root: Path, final_root: Path, origin: str) -> list[dict[str, str]]:
    unresolved: list[dict[str, str]] = []
    with closing(sqlite3.connect(database)) as connection, connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("工作区数据库完整性校验失败")
        if connection.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
            raise ValueError("工作区数据库版本高于当前程序")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column, hash_column in (("responses", "raw_path", "content_sha256"), ("artifacts", "local_path", "sha256")):
            if table not in tables:
                continue
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not {column, hash_column, "id"}.issubset(columns):
                unresolved.append({"table": table, "reason": "legacy_schema_requires_review"})
                continue
            for identity, value, digest in connection.execute(f"SELECT id,{column},{hash_column} FROM {table}"):
                if not value:
                    continue
                relative = _relative(str(value), origin) if origin else None
                if relative is None and not Path(str(value)).is_absolute() and ".." not in PurePosixPath(str(value)).parts and "\\" not in str(value) and ":" not in str(value):
                    relative = PurePosixPath(str(value)).parts
                if relative is None:
                    unresolved.append({"table": table, "id": str(identity), "reason": "external_or_unknown_origin"})
                    continue
                local = stage_root.joinpath(*relative)
                if local.is_file():
                    with local.open("rb") as stream:
                        actual = hashlib.file_digest(stream, "sha256").hexdigest()
                    if actual != digest:
                        raise ValueError("工作区数据库引用与文件哈希不一致")
                else:
                    unresolved.append({"table": table, "id": str(identity), "reason": "referenced_file_not_in_package"})
                connection.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (str(final_root.joinpath(*relative)), identity))
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("工作区数据库关联校验失败")
    return unresolved


def import_package(package: Path, destination: Path, *, expected_sha256: str = "") -> dict[str, Any]:
    """Publish only a completely verified copy, without modifying the source."""
    package, destination = package.resolve(), destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("工作区导入目标已存在，请选择新目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = destination.parent.resolve() / destination.name
    with tempfile.TemporaryDirectory(prefix=".omnicrawler-import-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        snapshot = stage / "package.zip"
        if shutil.disk_usage(destination.parent).free < package.stat().st_size:
            raise OSError("磁盘空间不足，无法暂存工作区包")
        shutil.copyfile(package, snapshot)
        with snapshot.open("rb") as stream:
            package_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if expected_sha256 and package_sha != expected_sha256:
            raise ValueError("工作区包已变化，请重新检查")
        project = stage / "project"
        project.mkdir()
        workspace = project / "workspace"
        workspace.mkdir()
        with zipfile.ZipFile(snapshot) as archive:
            members = validate_zip_archive(archive, required=("omnicrawler-package.json", "project/config.yaml", "project/workspace.json"))
            if any(members[name].file_size > DEFAULT_ZIP_READ_LIMITS.max_manifest_bytes for name in ("project/config.yaml", "project/workspace.json")):
                raise ValueError("工作区配置或元数据超过大小限制")
            manifest = json.loads(read_zip_member(archive, members["omnicrawler-package.json"], maximum_bytes=DEFAULT_ZIP_READ_LIMITS.max_manifest_bytes))
            if not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("kind") != "full-workspace":
                raise ValueError("仅支持完整工作区包导入")
            hashes = manifest.get("files")
            payloads = {name for name, info in members.items() if not info.is_dir() and name != "omnicrawler-package.json"}
            if not isinstance(hashes, dict) or set(hashes) != payloads:
                raise ValueError("工作区包文件集合与清单不一致")
            if any(not name.startswith("project/workspace/") and name not in {"project/config.yaml", "project/workspace.json"} for name in hashes):
                raise ValueError("工作区包包含未支持的文件位置")
            needed = sum(members[name].file_size for name in hashes)
            if shutil.disk_usage(destination.parent).free < needed:
                raise OSError("磁盘空间不足，无法导入工作区")
            for name, digest in hashes.items():
                if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValueError("工作区包哈希格式无效")
                archive_relative = PurePosixPath(name).relative_to("project")
                target = project.joinpath(*archive_relative.parts)
                if copy_zip_member(archive, members[name], target) != digest:
                    raise ValueError("工作区包文件哈希校验失败")
        config_path = project / "config.yaml"
        original = config_path.read_text(encoding="utf8")
        if not scan_config_text(original)["ok"]:
            raise ValueError("工作区包配置包含明文凭据，请先改用安全引用")
        raw = yaml.safe_load(original)
        if not isinstance(raw, dict) or not isinstance(raw.get("project", {}), dict):
            raise ValueError("工作区包配置结构无效")
        raw.setdefault("project", {})
        origin = str(manifest.get("workspace_origin") or "")
        if origin and not (PureWindowsPath(origin).is_absolute() or PurePosixPath(origin).is_absolute()):
            raise ValueError("工作区原位置必须是绝对路径")
        unresolved: list[dict[str, str]] = []
        final_workspace = destination / "workspace"
        for section_name, field in (("source", "query_file"), ("source", "spider_file"), ("download", "output_dir")):
            section = raw.get(section_name, {})
            value = section.get(field) if isinstance(section, dict) else None
            if not isinstance(value, str) or not value:
                continue
            candidate = value
            original_root = str(manifest.get("project_root_origin") or "")
            if original_root and not PureWindowsPath(value).is_absolute() and not PurePosixPath(value).is_absolute():
                path_type = PureWindowsPath if re.match(r"^[A-Za-z]:[\\/]", original_root) else PurePosixPath
                candidate = str(path_type(original_root) / value)
            relative = _relative(candidate, origin) if origin else None
            if relative is not None:
                section[field] = str(Path("workspace").joinpath(*relative))
                if field != "output_dir" and not workspace.joinpath(*relative).is_file():
                    unresolved.append({"config_field": section_name + "." + field, "reason": "referenced_file_not_in_package"})
            else:
                unresolved.append({"config_field": section_name + "." + field, "reason": "external_or_unknown_origin"})
        for database in workspace.rglob("*"):
            if not database.is_file():
                continue
            with database.open("rb") as stream:
                sqlite_header = stream.read(16) == b"SQLite format 3\0"
            if sqlite_header:
                unresolved.extend(_rebind_database(database, workspace, final_workspace, origin))
        # Explicit root avoids config discovery changing with its destination.
        raw["project"]["root"] = "."
        raw["project"]["workspace"] = "workspace"
        original_path = project / "config.before-relocation.yaml"
        original_path.write_text(original, encoding="utf8")
        config_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf8")
        report = {"format": 1, "imported_at": utcnow(), "package_sha256": package_sha,
                  "workspace_origin": origin, "workspace": str(final_workspace), "config": str(destination / "config.yaml"),
                  "identity": "persisted" if raw["project"].get("task_id") else "legacy_unverified",
                  "unresolved_references": unresolved, "exports_included": bool(manifest.get("exports_included", False)),
                  "credentials": "original_references_preserved"}
        (workspace / "relocation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        old_manifest = json.loads((project / "workspace.json").read_text(encoding="utf8"))
        if not isinstance(old_manifest, dict):
            raise ValueError("工作区元数据格式无效")
        old_manifest.update(config_path=str(destination / "config.yaml"), relocated_from=origin, updated_at=utcnow())
        (workspace / "workspace.json").write_text(json.dumps(old_manifest, ensure_ascii=False, indent=2), encoding="utf8")
        # rename to a fresh sibling is atomic; never use replace on user data.
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("工作区导入目标已存在，请选择新目录")
        project.rename(destination)
        return {"imported": str(destination), **report, "review_required": bool(unresolved) or not origin}
