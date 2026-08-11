from __future__ import annotations

import argparse
import os
import sys
import threading
from dataclasses import asdict
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Any

from ..services.application_service import ApplicationService
from .execution_backend import WorkerSession, _read_session, _write_session


class WorkerRuntime:
    def __init__(self, session_file: Path) -> None:
        self.session_file = session_file
        self.session = _read_session(session_file)
        self.service = ApplicationService(self.session.config_path)
        self.state: dict[str, Any] = {"status": "starting", "session_id": self.session.session_id}
        self._lock = threading.Lock()
        self._shutdown = threading.Event()

    def run(self) -> int:
        listener = Listener(
            self.session.address, family=self.session.family,
            authkey=self.session.auth_token.encode("utf-8"),
        )
        ready = WorkerSession(**{**asdict(self.session), "status": "running", "pid": os.getpid()})
        _write_session(self.session_file, ready)
        with self._lock:
            self.state = {"status": "running", "session_id": self.session.session_id, "pid": ready.pid}
        thread = threading.Thread(target=self._execute, name="omnicrawl-task", daemon=True)
        thread.start()
        try:
            while not self._shutdown.is_set():
                connection = listener.accept()
                try:
                    request = connection.recv()
                    connection.send(self._handle(request))
                except (EOFError, OSError) as exc:
                    try:
                        connection.send({"ok": False, "error": str(exc)})
                    except OSError:
                        pass
                finally:
                    connection.close()
        finally:
            listener.close()
            if self.session.family == "AF_UNIX":
                Path(self.session.address).unlink(missing_ok=True)
        return 0

    def _execute(self) -> None:
        try:
            result = self.service.run(callback=self._progress_to_stderr)
        except Exception as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict):
            with self._lock:
                self.state = dict(result)
        else:
            # S2.5.47：非 dict 返回正常置终态，不卡 running
            with self._lock:
                self.state = {
                    "status": "succeeded",
                    "result": {"status": "succeeded", "value": result},
                }

    def _progress_to_stderr(self, event: str, details: dict[str, Any]) -> None:
        """把采集进度事件打印为 'PROGRESS: pct% url' 到 stderr（进 local-worker.log）。

        供 GUI 端 LogParser 解析驱动进度条；CLI 用户走 run_task 路径不经此分支。
        """
        if event != "crawl_progress":
            return
        # 数值安全转换：processed/limit 可能以字符串形式经外部事件传入，避免 str*int / str 除法崩溃
        current = int(details.get("processed") or 0)
        total = int(details.get("limit") or details.get("total") or 0)
        url = (details.get("url") or "")[:70]
        pct = int(current * 100 / total) if total else 0
        print(f"PROGRESS: {pct}% {url}", file=sys.stderr, flush=True)

    def _handle(self, request: Any) -> dict[str, Any]:
        command = request.get("command") if isinstance(request, dict) else ""
        try:
            if command == "status":
                with self._lock:
                    result = dict(self.state)
            elif command in {"pause", "resume", "stop"}:
                result = getattr(self.service, command)()
                with self._lock:
                    self.state["control"] = result
            elif command == "shutdown":
                self._shutdown.set()
                result = {"shutdown": True}
            else:
                raise ValueError(f"未知Worker命令: {command}")
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    return WorkerRuntime(Path(args.session).resolve()).run()


if __name__ == "__main__":
    raise SystemExit(main())
