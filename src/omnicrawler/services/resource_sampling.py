"""Bounded, independently timed sampling of the application's process tree."""

from __future__ import annotations

import os
import threading
from typing import Any


class ProcessTreeSampler:
    scope = "process_tree_rss_v1"

    def __init__(self, interval: float = 0.25) -> None:
        self.interval = interval
        self.peak = 0
        self.samples = 0
        self.incomplete_samples = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample(self) -> None:
        try:
            import psutil

            owner = psutil.Process(os.getpid())
            processes: list[Any] = [owner, *owner.children(recursive=True)]
            seen: set[tuple[int, float]] = set()
            total = 0
            incomplete = False
            for process in processes:
                try:
                    identity = (process.pid, process.create_time())
                    if identity not in seen:
                        seen.add(identity)
                        total += process.memory_info().rss
                except psutil.NoSuchProcess:
                    continue  # Child exited between enumeration and measurement.
                except psutil.AccessDenied:
                    incomplete = True
            self.samples += 1
            self.peak = max(self.peak, total)
            self.incomplete_samples += int(incomplete)
        except (ImportError, OSError):
            self.incomplete_samples += 1
        except Exception:  # noqa: BLE001 - optional measurement must not stop the task
            self.incomplete_samples += 1

    def __enter__(self) -> ProcessTreeSampler:
        self.sample()
        self._thread = threading.Thread(target=self._loop, name="benchmark-resources", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.sample()

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self.sample()

    @property
    def complete(self) -> bool:
        return self.samples > 0 and self.incomplete_samples == 0
