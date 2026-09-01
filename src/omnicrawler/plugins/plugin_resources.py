"""Host-owned resource grants for isolated Contract 2 plugins.

Plugins receive opaque grant handles and relative names, never an unrestricted
filesystem root.  Grants are created by trusted host UI after an explicit user
choice; subprocess plugins can only inspect the already-granted tree.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_ENUMERATED_ITEMS = 2_000
MAX_SCAN_ENTRIES = 10_000
MAX_SCAN_DEPTH = 12
MAX_RESOURCE_READ_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ResourceGrant:
    handle: str
    root: Path
    label: str


def _is_link_like(path: Path) -> bool:
    """Reject symlinks and Windows junctions/reparse-directory aliases."""

    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return True


class ResourceGrantBroker:
    """Session-local, host-created directory grants with bounded reads."""

    def __init__(self) -> None:
        self._grants: dict[str, ResourceGrant] = {}

    def grant_directory(self, path: str | Path, *, label: str = "") -> str:
        candidate = Path(path).expanduser()
        if _is_link_like(candidate) or not candidate.is_dir():
            raise ValueError("资源授权目标必须是存在的真实目录，不能是符号链接或目录联接")
        root = candidate.resolve(strict=True)
        handle = "resource:" + secrets.token_urlsafe(24)
        self._grants[handle] = ResourceGrant(
            handle=handle,
            root=root,
            label=(label.strip() or root.name or "resource"),
        )
        return handle

    def discover_directory(self, kind: str, identifier: str) -> str:
        """Resolve one narrowly-scoped, user-triggered well-known resource location."""

        if str(kind).casefold() != "steam_workshop":
            raise ValueError("宿主不支持此资源发现器")
        app_id = str(identifier).strip()
        if not app_id.isascii() or not app_id.isdigit() or len(app_id) > 16:
            raise ValueError("Steam Workshop app_id 非法")
        libraries: list[Path] = []
        for variable in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(variable)
            if base:
                libraries.append(Path(base) / "Steam")
        steam_root = next((path for path in libraries if path.is_dir()), None)
        if steam_root is not None:
            config = steam_root / "steamapps" / "libraryfolders.vdf"
            try:
                text = config.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for match in re.finditer(r'"path"\s+"([^"]+)"', text):
                libraries.append(Path(match.group(1).replace("\\\\", "\\")))
        seen: set[Path] = set()
        for library in libraries:
            try:
                normalized = library.resolve()
            except OSError:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            candidate = normalized / "steamapps" / "workshop" / "content" / app_id
            if candidate.is_dir() and not _is_link_like(candidate):
                return self.grant_directory(candidate, label=f"Steam Workshop · {app_id}")
        raise ValueError("找不到指定文件夹；请确认应用已安装，或改用手动选择")

    def describe(self, handle: str) -> dict[str, Any]:
        grant = self._require(handle)
        return {"handle": grant.handle, "label": grant.label, "kind": "directory"}

    def enumerate(
        self,
        handle: str,
        *,
        relative: str = "",
        recursive: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        grant = self._require(handle)
        bounded_limit = int(limit)
        if not 1 <= bounded_limit <= MAX_ENUMERATED_ITEMS:
            raise ValueError(f"资源枚举 limit 必须介于 1 和 {MAX_ENUMERATED_ITEMS}")
        start = self._resolve(grant, relative, require_file=False)
        if not start.is_dir():
            raise ValueError("资源枚举目标不是目录")
        items: list[dict[str, Any]] = []
        examined = 0
        iterator: Any
        if recursive:
            iterator = os.walk(start, followlinks=False)
        else:
            try:
                children = sorted(start.iterdir(), key=lambda item: item.name.casefold())
            except OSError as exc:
                raise ValueError(f"资源目录无法读取: {exc}") from exc
            iterator = iter([(str(start), [], [item.name for item in children])])
        for directory, names, filenames in iterator:
            directory_path = Path(directory)
            depth = len(directory_path.relative_to(start).parts)
            if depth > MAX_SCAN_DEPTH:
                names[:] = []
                continue
            names[:] = sorted(
                name
                for name in names
                if not _is_link_like(directory_path / name)
            )
            entries = [*names, *sorted(filenames)]
            for name in entries:
                examined += 1
                if examined > MAX_SCAN_ENTRIES or len(items) >= bounded_limit:
                    return items
                path = directory_path / name
                if _is_link_like(path):
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(grant.root)
                    is_dir = resolved.is_dir()
                    is_file = resolved.is_file()
                    stat = resolved.stat()
                except (OSError, ValueError):
                    continue
                if not (is_dir or is_file):
                    continue
                items.append(
                    {
                        "name": name,
                        "relative": resolved.relative_to(grant.root).as_posix(),
                        "kind": "directory" if is_dir else "file",
                        "size": 0 if is_dir else int(stat.st_size),
                        "mtime_ns": int(stat.st_mtime_ns),
                    }
                )
        return items

    def read(self, handle: str, relative: str, *, maximum_bytes: int | None = None) -> bytes:
        grant = self._require(handle)
        limit = MAX_RESOURCE_READ_BYTES if maximum_bytes is None else int(maximum_bytes)
        if not 1 <= limit <= MAX_RESOURCE_READ_BYTES:
            raise ValueError(f"资源读取上限必须介于 1 和 {MAX_RESOURCE_READ_BYTES}")
        candidate = self._resolve(grant, relative, require_file=True)
        try:
            size = candidate.stat().st_size
            if size > limit:
                raise ValueError(f"资源超过单次读取上限: {size} > {limit}")
            return candidate.read_bytes()
        except OSError as exc:
            raise ValueError(f"资源读取失败: {exc}") from exc

    def resolve_media(self, handle: str, relative: str) -> Path:
        """Trusted host-only resolution for a media surface."""

        return self._resolve(self._require(handle), relative, require_file=True)

    def revoke(self, handle: str) -> None:
        self._grants.pop(handle, None)

    def _require(self, handle: str) -> ResourceGrant:
        grant = self._grants.get(str(handle))
        if grant is None:
            raise ValueError("资源句柄不存在、已撤销或不属于当前插件会话")
        return grant

    @staticmethod
    def _resolve(grant: ResourceGrant, relative: str, *, require_file: bool) -> Path:
        supplied = str(relative or "").replace("\\", "/")
        if (
            Path(supplied).is_absolute()
            or supplied.startswith("/")
            or re.match(r"^[a-zA-Z]:", supplied)
        ):
            raise ValueError("资源相对路径非法")
        parts = supplied.split("/") if supplied else []
        if any(part in {"", ".", ".."} or ":" in part for part in parts):
            raise ValueError("资源相对路径非法")
        candidate = grant.root.joinpath(*parts)
        cursor = grant.root
        for part in parts:
            cursor /= part
            if _is_link_like(cursor):
                raise ValueError("资源路径不能经过符号链接或目录联接")
        if _is_link_like(candidate):
            raise ValueError("资源路径不能是符号链接或目录联接")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(grant.root)
        except (OSError, ValueError) as exc:
            raise ValueError("资源路径不存在或逃逸授权目录") from exc
        if require_file and not resolved.is_file():
            raise ValueError("资源目标不是文件")
        return resolved
