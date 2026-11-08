"""``core.bounded_io.read_bounded``：有界读取必须记账，超限必须拒绝。

这些断言是「新调用点不能悄悄忘记记账」的护栏：只要 helper 被绕过或
记账顺序被改动，下面的用例就会红。
"""

from __future__ import annotations

import pytest

from omnicrawler.core.bounded_io import read_bounded
from omnicrawler.core.errors import ResponseTooLargeError


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def record_response(self, size: int, *, cost: float = 0.0, url: str = "") -> None:
        self.calls.append((size, url))


class _Response:
    def __init__(self, body: bytes, url: str = "https://example.com/data") -> None:
        self._body = body
        self._url = url

    def read(self, maximum: int) -> bytes:
        return self._body[:maximum]

    def geturl(self) -> str:
        return self._url


def test_reads_and_records_the_consumed_bytes() -> None:
    recorder = _Recorder()
    payload = read_bounded(_Response(b"hello"), 10, egress=recorder, label="测试响应")
    assert payload == b"hello"
    assert recorder.calls == [(5, "https://example.com/data")]


def test_reads_at_most_maximum_plus_one_byte() -> None:
    """只允许读 maximum+1：读满上限多一个字节就足以判定超限。"""

    class _Counting(_Response):
        def __init__(self) -> None:
            super().__init__(b"x" * 100)
            self.requested: list[int] = []

        def read(self, maximum: int) -> bytes:
            self.requested.append(maximum)
            return self._body[:maximum]

    response = _Counting()
    with pytest.raises(ResponseTooLargeError):
        read_bounded(response, 8, egress=_Recorder(), label="测试响应")
    assert response.requested == [9]


def test_exactly_maximum_bytes_is_accepted() -> None:
    recorder = _Recorder()
    payload = read_bounded(_Response(b"x" * 8), 8, egress=recorder, label="测试响应")
    assert payload == b"x" * 8
    assert recorder.calls == [(8, "https://example.com/data")]


def test_oversized_response_raises_with_label_and_limit() -> None:
    with pytest.raises(ResponseTooLargeError) as excinfo:
        read_bounded(_Response(b"x" * 9), 8, egress=_Recorder(), label="插件响应")
    message = str(excinfo.value)
    assert "插件响应" in message
    assert "8" in message


def test_oversized_response_is_still_recorded() -> None:
    """超限的字节确实过了网，必须进预算：只记成功读取会让大响应白嫖流量。"""
    recorder = _Recorder()
    with pytest.raises(ResponseTooLargeError):
        read_bounded(_Response(b"x" * 9), 8, egress=recorder, label="测试响应")
    assert recorder.calls == [(9, "https://example.com/data")]


def test_reverse_assertion_recording_is_not_optional() -> None:
    """反向断言：把记账挪到超限判断之后，上面的用例必须红。

    这里不写盘，只在内存里构造「漏记账」的实现，确认断言确实能抓住它。
    """

    def leaky_read_bounded(response, maximum: int, *, egress, label: str = "响应") -> bytes:
        payload = response.read(maximum + 1)
        if len(payload) > maximum:
            raise ResponseTooLargeError(f"{label}超过大小限制: > {maximum}")
        egress.record_response(len(payload), url=response.geturl())
        return payload

    recorder = _Recorder()
    with pytest.raises(ResponseTooLargeError):
        leaky_read_bounded(_Response(b"x" * 9), 8, egress=recorder, label="测试响应")
    assert recorder.calls == []  # 缺口原形：超限时没有记账

    real = _Recorder()
    with pytest.raises(ResponseTooLargeError):
        read_bounded(_Response(b"x" * 9), 8, egress=real, label="测试响应")
    assert real.calls != []  # 真实实现会记账


def test_records_the_url_reported_by_the_response_not_the_request() -> None:
    """重定向后 ``geturl()`` 才是真正出网的地址，记账要按它。"""
    recorder = _Recorder()
    read_bounded(_Response(b"ok", url="https://cdn.example.com/final"), 4, egress=recorder, label="测试响应")
    assert recorder.calls == [(2, "https://cdn.example.com/final")]
