"""Bounded reads that must be accounted for.

Every outbound HTTP path in this project has to answer the same three
questions: how much may I read, what do I do when the peer sends more, and who
records the bytes I actually consumed.  The surrounding code differs a lot
(streaming vs. one-shot, retry loops, JSON decoding, cookie jars), so there is
no honest single "do a request" helper.  This module keeps the one part that
*is* identical — read at most ``maximum + 1`` bytes, account for what was read,
and refuse the response when the peer sent more — in a single place so a new
call site cannot quietly forget the accounting step.

The accounting deliberately happens **before** the size check: those bytes did
cross the network, so they belong in the egress budget even though the caller
never sees them.  Recording only successful reads would let a peer that answers
with a huge body consume traffic that the budget never notices.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from .errors import ResponseTooLargeError


class EgressRecorder(Protocol):
    """The single method this module needs from an egress broker."""

    def record_response(self, size: int, *, cost: float = 0.0, url: str = "") -> None:
        """Account for ``size`` bytes read from ``url``."""


def read_bounded(
    response: Any,
    maximum: int,
    *,
    egress: EgressRecorder,
    label: str = "响应",
) -> bytes:
    """Read at most ``maximum`` bytes from ``response`` and account for them.

    ``response`` only needs ``read(n)`` and ``geturl()``; that keeps the helper
    usable with the fakes the AI/plugin tests already use.

    Raises:
        ResponseTooLargeError: the peer sent more than ``maximum`` bytes.  The
            bytes that were read are still recorded against the egress budget.
    """
    payload = cast(bytes, response.read(maximum + 1))
    egress.record_response(len(payload), url=response.geturl())
    if len(payload) > maximum:
        raise ResponseTooLargeError(f"{label}超过大小限制: > {maximum}")
    return payload
