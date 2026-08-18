from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.utils import atomic_write, utcnow


class RunControl:
    """Small cross-process control file used by the CLI and GUI to pause/resume safely."""

    def __init__(self, workspace: Path) -> None:
        self.path = workspace / "run_control.json"
        self._lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"paused": False, "stop_requested": False}
        return raw if isinstance(raw, dict) else {"paused": False, "stop_requested": False}

    def update(self, *, paused: bool | None = None, stop_requested: bool | None = None) -> dict[str, Any]:
        state = self.read()
        if paused is not None:
            state["paused"] = bool(paused)
        if stop_requested is not None:
            state["stop_requested"] = bool(stop_requested)
        state["updated_at"] = utcnow()
        with self._lock:
            atomic_write(self.path, json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))
        return state

    def reset(self) -> dict[str, Any]:
        return self.update(paused=False, stop_requested=False)

    def pause(self) -> dict[str, Any]:
        return self.update(paused=True)

    def resume(self) -> dict[str, Any]:
        return self.update(paused=False, stop_requested=False)

    def request_stop(self) -> dict[str, Any]:
        return self.update(stop_requested=True, paused=False)

    def wait_if_paused(
        self,
        *,
        notify: Callable[[str, dict[str, Any]], None] | None = None,
        poll_seconds: float = 0.25,
    ) -> bool:
        announced = False
        while True:
            state = self.read()
            if state.get("stop_requested"):
                return False
            if not state.get("paused"):
                if announced and notify:
                    notify("resumed", state)
                return True
            if not announced and notify:
                notify("paused", state)
                announced = True
            time.sleep(max(0.05, min(1.0, poll_seconds)))
