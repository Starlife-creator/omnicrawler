from __future__ import annotations

import hashlib
import mimetypes
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable

from ..core.config import AppConfig
from ..core.utils import atomic_write
from ..security.egress import EgressBroker


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    uri: str
    sha256: str
    size_bytes: int
    local_path: Path | None = None
    mirror_uri: str = ""


@runtime_checkable
class ObjectStore(Protocol):
    def put(self, key: str, payload: bytes, *, content_type: str = "") -> StoredObject: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


def safe_object_key(key: str) -> str:
    pure = PurePosixPath(str(key).replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ValueError(f"Unsafe object key: {key}")
    return "/".join(part for part in pure.parts if part not in {"", "."})


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / Path(*PurePosixPath(safe_object_key(key)).parts)).resolve()
        if self.root not in path.parents:
            raise ValueError(f"Object escaped storage root: {key}")
        return path

    def put(self, key: str, payload: bytes, *, content_type: str = "") -> StoredObject:
        key = safe_object_key(key)
        path = self._path(key)
        atomic_write(path, payload)
        return StoredObject(key, path.as_uri(), hashlib.sha256(payload).hexdigest(), len(payload), path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3ObjectStore:
    """Small S3 protocol adapter; boto3 is imported only when this backend is selected."""

    _client: Any = None
    _client_config_key: str | None = None
    _lock = threading.Lock()

    @classmethod
    def _get_client(cls, endpoint_url: str = "", region: str = "") -> Any:
        """Return a cached boto3 S3 client with connection pooling and adaptive retries.

        Uses double-checked locking for thread-safe lazy initialization.
        The client is shared across instances with the same configuration key.
        """
        config_key = f"s3:{endpoint_url}:{region}"
        if cls._client is not None and cls._client_config_key == config_key:
            return cls._client
        with cls._lock:
            if cls._client is not None and cls._client_config_key == config_key:
                return cls._client
            import boto3
            from botocore.config import Config

            options: dict[str, Any] = {}
            if endpoint_url:
                options["endpoint_url"] = endpoint_url
            if region:
                options["region_name"] = region
            options["config"] = Config(
                s3={"addressing_style": "path"},
                max_pool_connections=10,
                retries={"max_attempts": 3, "mode": "adaptive"},
            )
            cls._client = boto3.client("s3", **options)
            cls._client_config_key = config_key
            return cls._client

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        endpoint_url: str = "",
        region: str = "",
        client: Any = None,
        egress: EgressBroker | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket is required")
        if client is None:
            try:
                import boto3  # noqa: F401 — ensure dependency is available
            except ImportError as exc:
                raise RuntimeError("S3 storage requires: pip install omnicrawler-platform[storage]") from exc
            client = self._get_client(endpoint_url=endpoint_url, region=region)
        self.client = client
        self.bucket = bucket
        self.prefix = safe_object_key(prefix) if prefix else ""
        self.egress = egress
        self.endpoint = endpoint_url.rstrip("/") or (
            f"https://s3.{region}.amazonaws.com" if region else "https://s3.amazonaws.com"
        )

    def _sdk_request(self):
        if self.egress is None:
            from contextlib import nullcontext

            return nullcontext()
        return self.egress.sdk_request(self.endpoint, transport="boto3-s3")

    def _key(self, key: str) -> str:
        clean = safe_object_key(key)
        return f"{self.prefix}/{clean}" if self.prefix else clean

    def put(self, key: str, payload: bytes, *, content_type: str = "") -> StoredObject:
        remote_key = self._key(key)
        options: dict[str, Any] = {}
        guessed = content_type or mimetypes.guess_type(remote_key)[0] or "application/octet-stream"
        options["ContentType"] = guessed
        with self._sdk_request():
            self.client.put_object(Bucket=self.bucket, Key=remote_key, Body=payload, **options)
        if self.egress is not None:
            self.egress.record_response(0, cost=0.0, url=self.endpoint)
        return StoredObject(
            safe_object_key(key),
            f"s3://{self.bucket}/{remote_key}",
            hashlib.sha256(payload).hexdigest(),
            len(payload),
        )

    def get(self, key: str) -> bytes:
        with self._sdk_request():
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            payload = response["Body"].read()
        if self.egress is not None:
            self.egress.record_response(len(payload), url=self.endpoint)
        return payload

    def exists(self, key: str) -> bool:
        try:
            with self._sdk_request():
                self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception as exc:
            status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            raise


class MirroredObjectStore:
    """Keep a local recovery copy and synchronously mirror it to remote object storage."""

    def __init__(self, local: LocalObjectStore, remote: ObjectStore) -> None:
        self.local = local
        self.remote = remote

    def put(self, key: str, payload: bytes, *, content_type: str = "") -> StoredObject:
        local = self.local.put(key, payload, content_type=content_type)
        remote = self.remote.put(key, payload, content_type=content_type)
        return StoredObject(local.key, local.uri, local.sha256, local.size_bytes, local.local_path, remote.uri)

    def get(self, key: str) -> bytes:
        if self.local.exists(key):
            return self.local.get(key)
        return self.remote.get(key)

    def exists(self, key: str) -> bool:
        return self.local.exists(key) or self.remote.exists(key)


def build_object_store(config: AppConfig, egress: EgressBroker | None = None) -> ObjectStore:
    settings = config.section("storage").get("objects", {})
    if not isinstance(settings, dict):
        settings = {}
    local_root = config.workspace / str(settings.get("local_directory", "."))
    local = LocalObjectStore(local_root)
    backend = str(settings.get("backend", "local")).casefold()
    if backend == "local":
        return local
    if backend not in {"s3", "local+s3", "mirror"}:
        raise ValueError(f"Unsupported object storage backend: {backend}")
    remote = S3ObjectStore(
        str(settings.get("bucket", "")),
        str(settings.get("prefix", "")),
        endpoint_url=str(settings.get("endpoint_url", "")),
        region=str(settings.get("region", "")),
        egress=egress,
    )
    # A local recovery copy is mandatory because PDF workbench and desktop preview use paths.
    return MirroredObjectStore(local, remote)
