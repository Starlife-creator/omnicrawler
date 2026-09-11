"""CapabilityBroker 的「artifacts」能力域 —— 从 plugin_broker.py 抽出的 Mixin（P1-3 第四批）。

覆盖数据集产物读取与流式写入（open/write/commit/abort + 句柄管理）：
- ``_cap_artifacts_read``：已提交产物列表
- ``_cap_artifact_stream_open/write/commit/abort``：会话内流式写入，超限即 E_QUOTA
- ``_artifact_stream`` / ``_abort_artifact_stream``：句柄查找与清理（内部共享）

以普通 Mixin 形式保留 ``self`` 语义（broker 经 ``dispatch`` 的 getattr 路由调用
``_cap_*``，方法在实例上即可达），宿主属性与调用点不变。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from .plugin_broker_contracts import E_CONTRACT, E_INTERNAL, E_QUOTA, E_RESOURCE, CapabilityError

LOGGER = logging.getLogger(__name__)


class BrokerArtifactsMixin:
    """artifacts 能力域：产物读取与流式写入。"""

    # ---- 宿主契约：实例属性（由 CapabilityBroker.__init__ 建立）----
    _dataset: Any
    _artifact_root: Path
    _maximum_artifact_bytes: int
    _artifact_streams: dict[str, dict[str, Any]]
    committed_artifacts: list[dict[str, Any]]
    def _cap_artifacts_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._dataset is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 DatasetReader")
        infos = self._dataset.artifacts()
        return {"artifacts": [{"name": a.name, "size": a.size_bytes} for a in infos]}

    def _cap_artifact_stream_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        if len(self._artifact_streams) >= 8:
            raise CapabilityError(E_QUOTA, "单个插件会话最多同时打开 8 个工件流")
        name = str(payload.get("name", "")).strip()
        if (
            not name
            or len(name) > 180
            or Path(name).name != name
            or name in {".", ".."}
            or any(char in name for char in "\x00\r\n")
        ):
            raise CapabilityError(E_CONTRACT, "artifact.stream.open 文件名非法")
        media_type = str(payload.get("media_type", "application/octet-stream")).strip()
        if not media_type or len(media_type) > 200 or any(char in media_type for char in "\r\n"):
            raise CapabilityError(E_CONTRACT, "artifact.stream.open media_type 非法")
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        target = self._artifact_root / name
        if target.exists():
            raise CapabilityError(E_RESOURCE, f"工件已存在，拒绝覆盖: {name}")
        handle = secrets.token_urlsafe(24)
        partial = self._artifact_root / f".omnicrawler-{handle}.part"
        try:
            stream = partial.open("xb")
        except OSError as exc:
            raise CapabilityError(E_RESOURCE, f"无法创建工件流: {exc}") from exc
        self._artifact_streams[handle] = {
            "stream": stream,
            "partial": partial,
            "target": target,
            "name": name,
            "media_type": media_type,
            "size": 0,
            "sha256": hashlib.sha256(),
        }
        return {"handle": handle, "maximum_bytes": self._maximum_artifact_bytes}

    def _cap_artifact_stream_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        handle, entry = self._artifact_stream(payload)
        encoded = payload.get("content_b64")
        if not isinstance(encoded, str):
            raise CapabilityError(E_CONTRACT, "artifact.stream.write 需要 content_b64")
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, "artifact.stream.write content_b64 非法") from exc
        if len(chunk) > 1024 * 1024:
            raise CapabilityError(E_QUOTA, "单个工件写入分块不得超过 1 MiB")
        new_size = int(entry["size"]) + len(chunk)
        if new_size > self._maximum_artifact_bytes:
            self._abort_artifact_stream(handle)
            raise CapabilityError(E_QUOTA, "工件超过会话允许的最大字节数，未提交内容已删除")
        try:
            entry["stream"].write(chunk)
        except OSError as exc:
            self._abort_artifact_stream(handle)
            raise CapabilityError(E_RESOURCE, f"工件流写入失败: {exc}") from exc
        entry["sha256"].update(chunk)
        entry["size"] = new_size
        return {"written": len(chunk), "size": new_size}

    def _cap_artifact_stream_commit(self, payload: dict[str, Any]) -> dict[str, Any]:
        handle, entry = self._artifact_stream(payload)
        stream = entry["stream"]
        try:
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(entry["partial"], entry["target"])
        except OSError as exc:
            self._abort_artifact_stream(handle)
            raise CapabilityError(E_RESOURCE, f"工件提交失败: {exc}") from exc
        digest = entry["sha256"].hexdigest()
        result = {
            "artifact_id": "sha256:" + digest,
            "name": entry["name"],
            "media_type": entry["media_type"],
            "size": entry["size"],
            "sha256": digest,
        }
        self.committed_artifacts.append({**result, "path": str(entry["target"])})
        del self._artifact_streams[handle]
        return result

    def _cap_artifact_stream_abort(self, payload: dict[str, Any]) -> dict[str, Any]:
        handle, _entry = self._artifact_stream(payload)
        self._abort_artifact_stream(handle)
        return {"aborted": True}

    def _artifact_stream(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        handle = str(payload.get("handle", ""))
        entry = self._artifact_streams.get(handle)
        if entry is None:
            raise CapabilityError(E_CONTRACT, "未知或已关闭的工件流句柄")
        return handle, entry

    def _abort_artifact_stream(self, handle: str) -> None:
        entry = self._artifact_streams.pop(handle, None)
        if entry is None:
            return
        try:
            entry["stream"].close()
        finally:
            try:
                Path(entry["partial"]).unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("未能删除未提交插件工件: %s", entry["partial"])
