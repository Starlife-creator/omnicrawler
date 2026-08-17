from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from omnicrawler.services.storage_backends import (
    LocalObjectStore,
    MirroredObjectStore,
    S3ObjectStore,
    safe_object_key,
)


class FakeS3:
    def __init__(self) -> None:
        self.values = {}

    def put_object(self, **kwargs):
        self.values[(kwargs["Bucket"], kwargs["Key"])] = bytes(kwargs["Body"])

    def get_object(self, **kwargs):
        payload = self.values[(kwargs["Bucket"], kwargs["Key"])]
        return {"Body": type("Body", (), {"read": lambda self: payload})()}

    def head_object(self, **kwargs):
        if (kwargs["Bucket"], kwargs["Key"]) not in self.values:
            error = RuntimeError("missing")
            error.response = {"ResponseMetadata": {"HTTPStatusCode": 404}}
            raise error


def test_local_and_s3_mirror_round_trip(tmp_path) -> None:
    local = LocalObjectStore(tmp_path / "objects")
    fake = FakeS3()
    remote = S3ObjectStore("bucket", "project", client=fake)
    mirror = MirroredObjectStore(local, remote)

    stored = mirror.put("raw/page.html", b"hello", content_type="text/html")

    assert stored.local_path is not None and stored.local_path.read_bytes() == b"hello"
    assert stored.mirror_uri == "s3://bucket/project/raw/page.html"
    assert mirror.get("raw/page.html") == b"hello"


def test_object_keys_reject_traversal() -> None:
    with pytest.raises(ValueError):
        safe_object_key("../secret")
    with pytest.raises(ValueError):
        safe_object_key("/absolute")


def test_s3_sdk_calls_are_wrapped_by_audited_egress_boundary() -> None:
    broker = MagicMock()
    broker.sdk_request.return_value = nullcontext()
    fake = FakeS3()
    remote = S3ObjectStore(
        "bucket",
        client=fake,
        endpoint_url="https://objects.example.com",
        egress=broker,
    )
    remote.put("item.bin", b"payload")
    assert remote.get("item.bin") == b"payload"
    assert remote.exists("item.bin") is True
    assert broker.sdk_request.call_count == 3
    broker.sdk_request.assert_any_call(
        "https://objects.example.com", transport="boto3-s3"
    )


def test_s3_egress_denial_fails_closed() -> None:
    """出网授权拒绝 → S3 SDK 调用被阻断，对象未写入（fail-closed）。"""
    from omnicrawler.security.egress import EgressDisabledError

    broker = MagicMock()
    broker.sdk_request.side_effect = EgressDisabledError("denied for test")
    fake = FakeS3()
    remote = S3ObjectStore(
        "bucket",
        client=fake,
        endpoint_url="https://objects.example.com",
        egress=broker,
    )
    with pytest.raises(EgressDisabledError):
        remote.put("item.bin", b"payload")
    assert not fake.values
