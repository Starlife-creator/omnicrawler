from __future__ import annotations

import os
import re
from typing import Any

from .secrets_store import SecretsStore, SecretsStoreError  # noqa: F401  re-export 供调用方捕获

_SECRET_REF = re.compile(r"^secret://([A-Za-z0-9_.-]+)$")


def _env_name(name: str) -> str:
    return "OMNICRAWL_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def seal_secret(name: str, plaintext: str) -> str:
    """S2.2.2 出口加密：明文落入 secrets_store，返回 ``secret://<name>`` 引用。

    幂等：``plaintext`` 本身已是 secret:// 引用时原样返回（保存引用串不重复加密）。
    secrets_store 不可用时抛 SecretsStoreError，调用方不得回退写明文。
    """
    if _SECRET_REF.fullmatch(str(plaintext).strip()):
        return str(plaintext).strip()
    SecretsStore().set(name, str(plaintext))
    return f"secret://{name}"


def get_secret(name: str) -> str:
    # S1.3.8：兼容旧前缀 OMNICRAW_SECRET_*，新配置统一使用 OMNICRAWL_SECRET_*。
    for env_name in (_env_name(name), "OMNICRAW_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()):
        value = os.environ.get(env_name)
        if value is not None:
            return value
    try:
        import keyring
    except ImportError:
        pass
    else:
        value = keyring.get_password("omnicrawl", name)
        if value is not None:
            return value
    # S2.2 兜底：读 secrets_store（GUI 写密文，见 config_serializer.to_yaml）
    try:
        value = SecretsStore().get(name)
        if value is not None:
            return value
    except Exception:
        pass
    raise ValueError(
        f"凭据 {name!r} 未配置；请设置环境变量 {_env_name(name)}，"
        "或安装 keyring 后在系统凭据库中保存 omnicrawl 凭据。"
    )


def resolve_secret_refs(value: Any) -> Any:
    """递归展开 ``secret://<name>`` 引用（B05-006：带环检测，防无限递归）。"""

    def walk(item: Any, visited: frozenset[str]) -> Any:
        if isinstance(item, str):
            match = _SECRET_REF.fullmatch(item.strip())
            if not match:
                return item
            name = match.group(1)
            if name in visited:
                chain = " -> ".join([*visited, name])
                raise ValueError(f"secret:// 引用形成环: {chain}")
            return walk(get_secret(name), visited | {name})
        if isinstance(item, list):
            return [walk(child, visited) for child in item]
        if isinstance(item, dict):
            return {key: walk(child, visited) for key, child in item.items()}
        return item

    return walk(value, frozenset())
