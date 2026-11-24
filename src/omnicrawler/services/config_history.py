from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from ..core.utils import atomic_write, utcnow


def _version_key(path: Path) -> tuple[int, str]:
    """Order new snapshots by their embedded nanosecond clock and old ones by mtime."""
    parts = path.stem.rsplit("_", 2)
    if len(parts) == 3 and parts[1].isdigit():
        return int(parts[1]), path.name
    return path.stat().st_mtime_ns, path.name


class ConfigHistory:
    def __init__(self, root: Path, *, keep: int = 50) -> None:
        self.root = root
        self.keep = max(2, keep)

    def snapshot(self, config_path: Path, *, reason: str = "manual_save") -> Path | None:
        if not config_path.is_file():
            return None
        payload = config_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.root / config_path.stem
        directory.mkdir(parents=True, exist_ok=True)
        existing = sorted(directory.glob("*.yaml"), key=_version_key)
        if existing and hashlib.sha256(existing[-1].read_bytes()).hexdigest() == digest:
            return existing[-1]
        stamp = utcnow().replace(":", "-").replace("+", "_")
        target = directory / f"{stamp}_{time.time_ns():020d}_{digest[:10]}.yaml"
        shutil.copy2(config_path, target)
        meta = {"created_at": utcnow(), "source": str(config_path), "reason": reason, "sha256": digest}
        atomic_write(target.with_suffix(".json"), json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
        versions = sorted(directory.glob("*.yaml"), key=_version_key)
        for old in versions[:-self.keep]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
        return target

    def list(self, name: str) -> list[dict[str, Any]]:
        directory = self.root / Path(name).stem
        result: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.yaml"), key=_version_key, reverse=True):
            meta_path = path.with_suffix(".json")
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {"created_at": "", "reason": "unknown"}
            result.append({**meta, "path": str(path), "size_bytes": path.stat().st_size})
        return result

    def restore(self, version: Path, destination: Path, *, require_backup: bool = True) -> Path:
        """把历史版本复制到 ``destination``。

        ``require_backup=True``（默认）时，**覆盖前必须确认备份已存在**：
        若 ``destination`` 当前有内容，则要求调用方先用 :meth:`snapshot` 备份它，
        否则拒绝 —— 因为「恢复」是对用户不可逆的操作，而界面会承诺「可再次撤销」。

        ★ 为什么把这条放在这里而不是留給 GUI：`snapshot()` 在配置文件不存在时
        返回 ``None``（什么都不做），GUI 此前无条件往下走，于是「当前文件已被删除」
        这种情况下恢复**没有备份**却照样覆盖，而提示语仍写着「如需撤销，可再次打开
        配置历史」。这是纯粹的「承诺可按、实际不能」，判据属于数据层。
        """
        version = version.resolve()
        root = self.root.resolve()
        if root not in version.parents or not version.is_file():
            raise ValueError("Config history version is outside the history directory")
        if require_backup and destination.is_file():
            latest = self._latest_backup_for(destination)
            if latest is None or not self._same_bytes(latest, destination):
                raise ValueError(
                    "拒绝覆盖：当前配置尚未备份（恢复后无法撤销）。"
                )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(version, destination)
        return destination

    def _latest_backup_for(self, destination: Path) -> Path | None:
        """该配置文件最近一次快照路径；没有就返回 None。"""
        directory = self.root / destination.stem
        if not directory.is_dir():
            return None
        existing = sorted(directory.glob("*.yaml"), key=_version_key)
        return existing[-1] if existing else None

    @staticmethod
    def _same_bytes(left: Path, right: Path) -> bool:
        """备份是不是**当前内容的**备份（而不是更早版本的）。"""
        try:
            return hashlib.sha256(left.read_bytes()).hexdigest() == hashlib.sha256(
                right.read_bytes()
            ).hexdigest()
        except OSError:
            return False
