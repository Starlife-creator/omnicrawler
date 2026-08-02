from __future__ import annotations

import os
import re
from typing import Any

_SECRET_REF = re.compile(r"^secret://([A-Za-z0-9_.-]+)$")


def get_secret(name: str) -> str:
    env_name = "OMNICRAW_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()
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
    raise ValueError(
        f"凭据 {name!r} 未配置；请设置环境变量 {env_name}，"
        "或安装 keyring 后在系统凭据库中保存 omnicrawl 凭据。"
    )


def resolve_secret_refs(value: Any) -> Any:
    if isinstance(value, str):
        match = _SECRET_REF.fullmatch(value.strip())
        return get_secret(match.group(1)) if match else value
    if isinstance(value, list):
        return [resolve_secret_refs(item) for item in value]
    if isinstance(value, dict):
        return {key: resolve_secret_refs(item) for key, item in value.items()}
    return value
