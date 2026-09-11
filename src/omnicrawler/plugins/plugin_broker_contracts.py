"""能力契约叶子模块：版本协议、错误码、CapabilityError 与 required_capabilities 校验。

从 plugin_broker.py 迁出（P1-3 第一批）。无反向依赖（不导入任何包内模块），
可独立单测；plugin_broker.py 与 plugin_market_logic.py 等按函数内懒加载导入 validate。
"""
from __future__ import annotations

import re
from typing import Any

# Contract 2 capability protocol versions.  A version changes only when the
# request/response semantics change incompatibly; adding optional fields does
# not bump it.  Plugins discover this mapping through ``system.info`` and may
# declare fail-closed requirements in PLUGIN_METADATA.required_capabilities.
CAPABILITY_VERSIONS: dict[str, int] = {
    "system.info": 1,
    "records.read": 1,
    "records.page": 1,
    "records.write": 1,
    "responses.page": 1,
    "responses.payload": 1,
    "state.get": 1,
    "state.set": 1,
    "state.delete": 1,
    "state.migrate": 1,
    "artifacts.read": 1,
    "artifact.stream.open": 1,
    "artifact.stream.write": 1,
    "artifact.stream.commit": 1,
    "artifact.stream.abort": 1,
    "network.fetch": 1,
    "temp.open": 1,
    "files.read": 1,
    "resources.describe": 1,
    "resources.enumerate": 1,
    "resources.read": 1,
    "render.html.snapshot": 1,
    "render.html.live.start": 1,
    "render.html.live.stop": 1,
    "surface.background.set": 2,
    "surface.background.configure": 2,
    "surface.background.clear": 1,
    "surface.background.capabilities": 1,
    "secrets.get": 1,
}

_CAPABILITY_REQUIREMENT = re.compile(r"^(?:>=)?([1-9][0-9]*)$")


def validate_required_capabilities(required: dict[str, Any]) -> None:
    """Reject a Contract 2 plugin whose broker protocol cannot satisfy it."""

    for name, raw_requirement in required.items():
        capability = str(name).strip()
        match = _CAPABILITY_REQUIREMENT.fullmatch(str(raw_requirement).strip())
        if not capability or match is None:
            raise ValueError(
                f"能力版本要求非法: {name!r}={raw_requirement!r}（仅支持正整数或 >=正整数）"
            )
        minimum = int(match.group(1))
        available = CAPABILITY_VERSIONS.get(capability)
        if available is None:
            raise ValueError(f"宿主不支持插件要求的能力: {capability}")
        if available < minimum:
            raise ValueError(
                f"宿主能力版本不足: {capability}>={minimum}，当前为 {available}"
            )

# 能力 → 所需 manifest 权限（None = 内置，无需声明）
_CAPABILITY_PERMISSIONS: dict[str, str | None] = {
    "records.read": "records:read",
    "records.page": "records:read",
    "records.write": "records:write",
    "responses.page": "responses:read",
    "responses.payload": "responses:payload",
    "state.get": "state:read",
    "state.set": "state:write",
    "state.delete": "state:write",
    "state.migrate": "state:write",
    "artifacts.read": "artifacts:read",
    "artifact.stream.open": "artifacts:write",
    "artifact.stream.write": "artifacts:write",
    "artifact.stream.commit": "artifacts:write",
    "artifact.stream.abort": "artifacts:write",
    "network.fetch": "network:scoped",
    "temp.open": "temp:write",
    "files.read": "files:read",
    "resources.describe": "resources:read",
    "resources.enumerate": "resources:read",
    "resources.read": "resources:read",
    "render.html.snapshot": "render:local",
    "render.html.live.start": "render:scripted",
    "render.html.live.stop": "render:scripted",
    "surface.background.set": "surfaces:background",
    "surface.background.configure": "surfaces:background",
    "surface.background.clear": "surfaces:background",
    "surface.background.capabilities": "surfaces:background",
    # O 例外路径（方案 O2 方案 B）：secrets.get 需 manifest 声明 secrets 白名单；
    # 默认路径是网络经宿主代理密钥零暴露（O2 方案 C），secrets.get 仅显式例外。
    "secrets.get": "secrets:read",
    "system.info": None,
}

E_CONTRACT = "E_CONTRACT"
E_PERMISSION = "E_PERMISSION"
E_QUOTA = "E_QUOTA"
E_RESOURCE = "E_RESOURCE"
E_INTERNAL = "E_INTERNAL"
# Phase 2b J2：data_egress_policy=block 档的共现阻断错误码（C4 权威清单第 73 轮）。
E_EGRESS_BLOCKED = "E_EGRESS_BLOCKED"


class CapabilityError(Exception):
    """带协议错误码的能力代理异常（broker 转成 ok=false 响应）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
