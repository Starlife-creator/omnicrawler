"""Resolve async AI destinations through the same DNS policy as sync clients."""
from __future__ import annotations

import asyncio
import socket
from typing import Any

from aiohttp.abc import AbstractResolver, ResolveResult


class PolicyResolver(AbstractResolver):
    def __init__(self, policy: Any) -> None:
        self.policy = policy

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[ResolveResult]:
        addresses = await asyncio.to_thread(self.policy.approved_addresses, host, port)
        return [{"hostname": host, "host": address, "port": port,
                 "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                 "proto": 0, "flags": socket.AI_NUMERICHOST} for address in addresses]

    async def close(self) -> None:
        return None
