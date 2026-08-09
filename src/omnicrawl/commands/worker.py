from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import load_config
from ..runtime.execution_backend import LocalWorkerBackend


def execute(config: str, action: str, *, session: str = "") -> dict[str, Any]:
    backend = LocalWorkerBackend()
    if action == "start":
        return backend.start(config)
    session_file = Path(session).expanduser().resolve() if session else load_config(config).workspace / "worker-session.json"
    backend.attach(session_file)
    if action == "status":
        return backend.status()
    if action in {"pause", "resume", "stop", "shutdown"}:
        return getattr(backend, action)()
    raise ValueError(f"未知Worker操作: {action}")
